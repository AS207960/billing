from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import transaction
from django.contrib.auth import get_user_model
import pika.exceptions
import ipaddress
import threading
import decimal
import time
import traceback
import sys
import functools
from billing import models, views, tasks, vat, apps, cf
import billing.proto.billing_pb2
import billing.proto.geoip_pb2


class Command(BaseCommand):
    help = 'Runs the RPC server on rabbitmq'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue = {}
        self.parameters = pika.URLParameters(settings.RABBITMQ_RPC_URL)
        self.connection = None
        self.channel = None

    def setup_connection(self):
        self.connection = pika.BlockingConnection(parameters=self.parameters)
        self.channel = self.connection.channel()
        self.channel.queue_declare(queue='billing_rpc', durable=True)
        self.channel.basic_qos(prefetch_count=5, global_qos=True)
        self.channel.basic_consume(queue='billing_rpc', on_message_callback=self.callback, auto_ack=False)

    def handle(self, *args, **options):
        self.setup_connection()

        print("RPC handler now running", flush=True)

        try:
            while True:
                try:
                    while True:
                        try:
                            self.connection.process_data_events()
                        except pika.exceptions.AMQPConnectionError as e:
                            traceback.print_exc()
                            print(f"Connection dropped: {e}")
                            sys.stdout.flush()
                            sys.stderr.flush()
                            self.setup_connection()
                        time.sleep(0.1)
                except pika.exceptions.AMQPError:
                    traceback.print_exc()
                    sys.stdout.flush()
                    sys.stderr.flush()
                    time.sleep(5)
                    self.setup_connection()

        except (KeyboardInterrupt, SystemExit):
            print("Exiting...")
            return

    def callback(self, channel, method, properties, body):
        t = threading.Thread(target=self._callback, args=(channel, method, properties, body))
        t.setDaemon(True)
        t.start()

    def nack(self, channel, tag):
        self.connection.add_callback_threadsafe(
            functools.partial(channel.basic_nack, delivery_tag=tag)
        )

    def ack(self, channel, tag):
        self.connection.add_callback_threadsafe(
            functools.partial(channel.basic_ack, delivery_tag=tag)
        )

    @staticmethod
    def resp(channel, resp, properties, delivery_tag):
        channel.basic_publish(
            exchange='',
            routing_key=properties.reply_to,
            properties=pika.BasicProperties(correlation_id=properties.correlation_id),
            body=resp.SerializeToString()
        )
        channel.basic_ack(delivery_tag=delivery_tag)

    def _callback(self, channel, method, properties, body):
        msg = billing.proto.billing_pb2.BillingRequest()
        msg.ParseFromString(body)

        msg_type = msg.WhichOneof("message")
        if msg_type == "convert_currency":
            print(f"{properties.correlation_id} - Received currency conversion request\n{msg.convert_currency}", flush=True)
            try:
                resp = self.convert_currency(msg.convert_currency)
            except:
                traceback.print_exc()
                sys.stdout.flush()
                sys.stderr.flush()
                self.nack(channel, method.delivery_tag)
                return
        elif msg_type == "charge_user":
            print(f"{properties.correlation_id} - Received charge request\n{msg.charge_user}", flush=True)
            try:
                resp = self.charge_user(msg.charge_user)
            except:
                traceback.print_exc()
                sys.stdout.flush()
                sys.stderr.flush()
                self.nack(channel, method.delivery_tag)
                return
        elif msg_type == "cloudflare_account":
            print(f"{properties.correlation_id} - Received cloudflare account request\n{msg.cloudflare_account}", flush=True)
            try:
                resp = self.cloudflare_account(msg.cloudflare_account)
            except:
                traceback.print_exc()
                sys.stdout.flush()
                sys.stderr.flush()
                self.nack(channel, method.delivery_tag)
                return
        else:
            print(f"{properties.correlation_id} - Received unknown request\n{msg}", flush=True)
            self.ack(channel, method.delivery_tag)
            return

        print(f"{properties.correlation_id} - Sending response\n{resp}", flush=True)

        self.connection.add_callback_threadsafe(functools.partial(
            self.resp, channel=channel, properties=properties, delivery_tag=method.delivery_tag, resp=resp
        ))

    @staticmethod
    def convert_currency(msg: billing.proto.billing_pb2.ConvertCurrencyRequest) \
            -> billing.proto.billing_pb2.ConvertCurrencyResponse:
        amount = decimal.Decimal(msg.amount) / decimal.Decimal(100)

        billing_address_country = None
        billing_address_postal_code = None
        if msg.HasField("country_selection"):
            billing_address_country = msg.country_selection.value.lower()

        if msg.HasField("username"):
            account = models.Account.objects.filter(user__username=msg.username.value).first()
            if account and not billing_address_country and account.billing_address:
                billing_address_country = account.billing_address.country_code.code.lower()
                billing_address_postal_code = account.billing_address.postal_code
        else:
            account = None

        if not billing_address_country and msg.HasField("remote_ip"):
            try:
                ip_address = ipaddress.ip_address(msg.remote_ip.value)
            except ValueError:
                pass
            else:
                ip_req = billing.proto.geoip_pb2.GeoIPRequest()
                ip_res = billing.proto.geoip_pb2.IPLookupResponse()
                if isinstance(ip_address, ipaddress.IPv4Address):
                    ip_req.ip_lookup.ipv4_addr = int(ip_address)
                else:
                    ip_req.ip_lookup.ipv6_addr = int(ip_address).to_bytes(16, "big")
                ip_res.ParseFromString(apps.rpc_client.call("geoip_rpc", ip_req.SerializeToString(), timeout=3))
                if ip_res.status == billing.proto.geoip_pb2.IPLookupResponse.OK:
                    if ip_res.data.HasField("country"):
                        billing_address_country = ip_res.data.country.value.lower()
                    if ip_res.data.HasField("postal_code"):
                        billing_address_postal_code = ip_res.data.postal_code.value

        if not billing_address_country:
            billing_address_country = "gb"

        if msg.to_currency != "":
            to_currency = msg.to_currency
        else:
            to_currency = vat.COUNTRY_CURRENCIES.get(billing_address_country, None)
            if to_currency is None:
                to_currency = "GBP"

        try:
            amount = models.ExchangeRate.get_rate(msg.from_currency, to_currency) * amount
        except models.ExchangeRate.DoesNotExist:
            return billing.proto.billing_pb2.ConvertCurrencyResponse()
        amount_vat = amount

        country_vat_rate = vat.get_vat_rate(billing_address_country, billing_address_postal_code)
        if country_vat_rate is not None:
            amount_vat += (amount * country_vat_rate)

        return billing.proto.billing_pb2.ConvertCurrencyResponse(
            amount=int(amount * decimal.Decimal(100)),
            amount_inc_vat=int(amount_vat * decimal.Decimal(100)),
            taxable=account.taxable if account else True,
            used_country=billing_address_country,
            currency=to_currency
        )

    @staticmethod
    def charge_user(msg: billing.proto.billing_pb2.ChargeUserRequest) \
            -> billing.proto.billing_pb2.ChargeUserResponse:
        user = get_user_model().objects.filter(username=msg.user_id).first()
        account = user.account if user else None  # type: models.Account

        return_uri = msg.return_uri.value if msg.HasField("return_uri") else None
        notif_queue = msg.notif_queue.value if msg.HasField("notif_queue") else None
        amount = decimal.Decimal(msg.amount) / decimal.Decimal(100)

        with transaction.atomic():
            try:
                charge_state = tasks.charge_account(
                    account, amount, msg.descriptor, msg.id, can_reject=msg.can_reject, off_session=msg.off_session,
                    return_uri=return_uri, notif_queue=notif_queue, supports_delayed=notif_queue is not None
                )
            except tasks.ChargeError as e:
                return billing.proto.billing_pb2.ChargeUserResponse(
                    charge_state_id=str(e.charge_state.id),
                    result=billing.proto.billing_pb2.ChargeUserResponse.FAIL,
                    message=e.message
                )
            except tasks.ChargeStateRequiresActionError as e:
                return billing.proto.billing_pb2.ChargeUserResponse(
                    charge_state_id=str(e.charge_state.id),
                    result=billing.proto.billing_pb2.ChargeUserResponse.REDIRECT,
                    redirect_uri=e.redirect_url
                )

            return billing.proto.billing_pb2.ChargeUserResponse(
                charge_state_id=str(charge_state.id),
                result=billing.proto.billing_pb2.ChargeUserResponse.SUCCESS,
                state=tasks.charge_state_to_proto_enum(charge_state.ledger_item.state)
            )

    @staticmethod
    def cloudflare_account(msg: billing.proto.billing_pb2.CloudflareAccountRequest) \
            -> billing.proto.billing_pb2.CloudflareAccountResponse:
        user = get_user_model().objects.filter(username=msg.user_id).first()
        account = user.account if user else None  # type: models.Account

        if not account:
            return billing.proto.billing_pb2.CloudflareAccountResponse(
                result=billing.proto.billing_pb2.CloudflareAccountResponse.FAIL,
                message="Account not found"
            )

        res = cf.setup_cloudflare_account(account)

        if res == cf.CloudflareResult.SUCCESS:
            result_code = billing.proto.billing_pb2.CloudflareAccountResponse.SUCCESS
        elif res == cf.CloudflareResult.FAILURE:
            result_code = billing.proto.billing_pb2.CloudflareAccountResponse.FAIL
        elif res == cf.CloudflareResult.NEEDS_SETUP:
            result_code = billing.proto.billing_pb2.CloudflareAccountResponse.NEEDS_SETUP
        else:
            result_code = billing.proto.billing_pb2.CloudflareAccountResponse.FAIL

        return billing.proto.billing_pb2.CloudflareAccountResponse(
            result=result_code,
            account_id=res.account_id,
            message=res.message
        )