from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import transaction
from django.contrib.auth import get_user_model
import pika
import ipaddress
import decimal
from billing import models, views, tasks, vat, apps
import billing.proto.billing_pb2
import billing.proto.geoip_pb2


class Command(BaseCommand):
    help = 'Runs the RPC server on rabbitmq'

    def handle(self, *args, **options):
        parameters = pika.URLParameters(settings.RABBITMQ_RPC_URL)
        connection = pika.BlockingConnection(parameters=parameters)
        channel = connection.channel()

        channel.queue_declare(queue='billing_rpc', durable=True)

        channel.basic_qos(prefetch_count=1)
        channel.basic_consume(queue='billing_rpc', on_message_callback=self.callback, auto_ack=False)

        print("RPC handler now running")
        try:
            channel.start_consuming()
        except (KeyboardInterrupt, SystemExit):
            print("Exiting...")
            return

    def callback(self, channel, method, properties, body):
        msg = billing.proto.billing_pb2.BillingRequest()
        msg.ParseFromString(body)

        msg_type = msg.WhichOneof("message")
        if msg_type == "convert_currency":
            resp = self.convert_currency(msg.convert_currency)
        elif msg_type == "charge_user":
            resp = self.charge_user(msg.charge_user)
        else:
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        channel.basic_publish(
            exchange='',
            routing_key=properties.reply_to,
            properties=pika.BasicProperties(correlation_id=properties.correlation_id),
            body=resp.SerializeToString()
        )
        channel.basic_ack(delivery_tag=method.delivery_tag)

    @staticmethod
    def convert_currency(msg: billing.proto.billing_pb2.ConvertCurrencyRequest) \
            -> billing.proto.billing_pb2.ConvertCurrencyResponse:
        amount = decimal.Decimal(msg.amount) / decimal.Decimal(100)
        amount = models.ExchangeRate.get_rate(msg.from_currency, msg.to_currency) * amount
        amount_vat = amount

        billing_address_country = None
        if msg.HasField("country_selection"):
            billing_address_country = msg.country_selection.value.lower()

        if msg.HasField("username"):
            account = models.Account.objects.filter(user__username=msg.username.value).first()
            if not billing_address_country and account.billing_address:
                billing_address_country = account.billing_address.country_code.code.lower()
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
                ip_res.ParseFromString(apps.rpc_client.call("geoip_rpc", ip_req.SerializeToString()))
                if ip_res.status == billing.proto.geoip_pb2.IPLookupResponse.OK:
                    if ip_res.data.HasField("country"):
                        billing_address_country = ip_res.data.country.value.lower()

        if not billing_address_country:
            billing_address_country = "gb"

        country_vat_rate = vat.get_vat_rate(billing_address_country)
        if country_vat_rate is not None:
            amount_vat += (amount * country_vat_rate)

        return billing.proto.billing_pb2.ConvertCurrencyResponse(
            amount=int(amount * decimal.Decimal(100)),
            amount_inc_vat=int(amount_vat * decimal.Decimal(100)),
            taxable=account.taxable if account else True,
            used_country=billing_address_country
        )

    def charge_user(self, msg: billing.proto.billing_pb2.ChargeUserRequest) \
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
                    return_uri=return_uri, notif_queue=notif_queue, supports_delayed=True
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
            )
