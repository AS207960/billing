import datetime
import decimal
import json
import threading

import django.core.exceptions
import google.protobuf.wrappers_pb2
import pika
import pywebpush
import sentry_sdk
import stripe.error
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.shortcuts import reverse
from django.template.loader import render_to_string
from django.utils import timezone

from . import models, flux, utils, apps, vat
from .proto import billing_pb2

pika_parameters = pika.URLParameters(settings.RABBITMQ_RPC_URL)

SUBSCRIPTION_RETRY_ATTEMPTS = 3
SUBSCRIPTION_RETRY_INTERVAL = datetime.timedelta(days=2)


def as_thread(fun):
    def new_fun(*args, **kwargs):
        t = threading.Thread(target=fun, args=args, kwargs=kwargs)
        t.setDaemon(False)
        t.start()

    return new_fun


def mail_notif(ledger_item: models.LedgerItem, state_name: str, emoji: str):
    try:
        charge_state = ledger_item.charge_state
    except django.core.exceptions.ObjectDoesNotExist:
        try:
            charge_state = ledger_item.charge_state_payment
        except django.core.exceptions.ObjectDoesNotExist:
            charge_state = None

    if charge_state and charge_state.ledger_item.state != charge_state.ledger_item.STATE_FAILED:
        charge_state_url = settings.EXTERNAL_URL_BASE + reverse('complete_order', args=(charge_state.id,))
    else:
        charge_state_url = None

    context = {
        "name": ledger_item.account.user.first_name,
        "item": ledger_item,
        "charge_state_url": charge_state_url
    }
    html_content = render_to_string("billing_email/billing_notif.html", context)
    txt_content = render_to_string("billing_email/billing_notif.txt", context)

    subject = f"{emoji} {ledger_item.descriptor}: {state_name}" if state_name \
        else f"{emoji} {ledger_item.descriptor}"

    email = EmailMultiAlternatives(
        subject=subject,
        body=txt_content,
        to=[ledger_item.account.user.email],
        bcc=['q@as207960.net'],
        reply_to=['hello@glauca.digital']
    )
    email.attach_alternative(html_content, "text/html")
    email.send()


@as_thread
def mail_subscription_success(subscription: models.Subscription, value: decimal.Decimal):
    context = {
        "name": subscription.account.user.first_name,
        "plan_name": subscription.plan.name,
        "value": value
    }
    html_content = render_to_string("billing_email/billing_success.html", context)
    txt_content = render_to_string("billing_email/billing_success.txt", context)

    email = EmailMultiAlternatives(
        subject='Subscription payment successful',
        body=txt_content,
        to=[subscription.account.user.email],
        bcc=['q@as207960.net'],
        reply_to=['hello@glauca.digital']
    )
    email.attach_alternative(html_content, "text/html")
    email.send()


@as_thread
def mail_subscription_past_due(subscription: models.Subscription, value: decimal.Decimal, reason: str):
    context = {
        "name": subscription.account.user.first_name,
        "plan_name": subscription.plan.name,
        "value": value,
        "reason": reason
    }
    html_content = render_to_string("billing_email/billing_past_due.html", context)
    txt_content = render_to_string("billing_email/billing_past_due.txt", context)

    email = EmailMultiAlternatives(
        subject='Subscription payment failed',
        body=txt_content,
        to=[subscription.account.user.email],
        bcc=['q@as207960.net'],
        reply_to=['hello@glauca.digital']
    )
    email.attach_alternative(html_content, "text/html")
    email.send()


@as_thread
def mail_subscription_cancelled(subscription: models.Subscription, value: decimal.Decimal, reason: str):
    context = {
        "name": subscription.account.user.first_name,
        "plan_name": subscription.plan.name,
        "value": value,
        "reason": reason
    }
    html_content = render_to_string("billing_email/billing_cancelled.html", context)
    txt_content = render_to_string("billing_email/billing_cancelled.txt", context)

    email = EmailMultiAlternatives(
        subject='Subscription cancelled',
        body=txt_content,
        to=[subscription.account.user.email],
        bcc=['q@as207960.net'],
        reply_to=['hello@glauca.digital']
    )
    email.attach_alternative(html_content, "text/html")
    email.send()


@as_thread
def alert_account(account: models.Account, ledger_item: models.LedgerItem, new=False):
    extra = None
    if ledger_item.type == ledger_item.TYPE_CHARGE:
        if ledger_item.amount <= 0:
            emoji = "📉"
            body = f"£{-ledger_item.amount:.2f} for {ledger_item.descriptor}"
        else:
            emoji = "📈"
            body = f"£{ledger_item.amount:.2f} refund for {ledger_item.descriptor}"
    else:
        emoji = "💸"
        body = f"£{ledger_item.amount:.2f} from {ledger_item.descriptor}"

    if ledger_item.state == ledger_item.STATE_PENDING:
        extra = "pending"
        emoji = "⌛"
    elif ledger_item.state in (ledger_item.STATE_PROCESSING, ledger_item.STATE_PROCESSING_CANCELLABLE):
        extra = "processing"
        emoji = "📇"
    elif ledger_item.state == ledger_item.STATE_FAILED:
        extra = "failed"
        emoji = "🙅"
    elif ledger_item.state == ledger_item.STATE_COMPLETED and not new:
        extra = "completed"

    message = f"{emoji} {body}"
    if extra:
        message += f": {extra}"

    if ledger_item.amount != 0:
        mail_notif(ledger_item, extra, emoji)

    for subscription in account.notificationsubscription_set.all():
        try:
            pywebpush.webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {
                        "p256dh": subscription.key_p256dh,
                        "auth": subscription.key_auth
                    }
                },
                data=json.dumps({
                    "message": message
                }),
                vapid_private_key=settings.PUSH_PRIV_KEY,
                vapid_claims={"sub": "mailto:noc@as207960.net"},
            )
        except pywebpush.WebPushException as e:
            if e.response.status_code in [404, 410, 301]:
                subscription.delete()
            elif e.response.status_code in [401, 500, 502, 503]:
                pass
            else:
                sentry_sdk.capture_exception(e)


@receiver(pre_save, sender=models.LedgerItem)
def send_item_notif(sender, instance: models.LedgerItem, **kwargs):
    old_instance = models.LedgerItem.objects.filter(id=instance.id).first()
    if not old_instance:
        instance.last_state_change_timestamp = timezone.now()
        alert_account(instance.account, instance, new=True)
    elif old_instance.state != instance.state:
        instance.last_state_change_timestamp = timezone.now()
        alert_account(instance.account, instance)

    try:
        as_thread(flux.send_charge_state_notif)(instance.charge_state)
    except django.core.exceptions.ObjectDoesNotExist:
        pass
    try:
        as_thread(flux.send_charge_state_notif)(instance.charge_state_payment)
    except django.core.exceptions.ObjectDoesNotExist:
        pass


@receiver(post_save, sender=models.LedgerItem)
def try_update_charge_state(sender, instance: models.LedgerItem, **kwargs):
    try:
        charge_state = instance.charge_state_payment
    except django.core.exceptions.ObjectDoesNotExist:
        charge_state = None

    if charge_state and charge_state.ledger_item:
        if charge_state.ready_to_complete and instance.state == instance.STATE_COMPLETED and \
                charge_state.ledger_item and charge_state.account.balance >= (-charge_state.ledger_item.amount):
            charge_state.ledger_item.state = charge_state.ledger_item.STATE_COMPLETED
            charge_state.ledger_item.save()

        if instance.state in (instance.STATE_PROCESSING, instance.STATE_PROCESSING_CANCELLABLE):
            charge_state.ledger_item.state = instance.STATE_PROCESSING
            charge_state.ledger_item.save()

        if instance.state == instance.STATE_PENDING:
            charge_state.ledger_item.state = instance.STATE_PENDING
            charge_state.ledger_item.save()

        if instance.state == instance.STATE_FAILED:
            if charge_state.can_reject:
                charge_state.ledger_item.state = instance.STATE_PENDING
                charge_state.ledger_item.save()
            else:
                charge_state.ledger_item.state = instance.STATE_COMPLETED
                charge_state.ledger_item.save()
            charge_state.payment_ledger_item = None
            charge_state.save()

    try:
        charge_state_2 = instance.charge_state
    except django.core.exceptions.ObjectDoesNotExist:
        charge_state_2 = None

    if charge_state_2:
        if instance.state == instance.STATE_COMPLETED and not charge_state_2.completed_timestamp:
            charge_state_2.completed_timestamp = timezone.now()
            charge_state_2.save()

    try:
        subscription_charge = instance.subscriptioncharge
    except django.core.exceptions.ObjectDoesNotExist:
        subscription_charge = None

    if subscription_charge:
        if subscription_charge.is_setup_charge:
            if instance.state == instance.STATE_COMPLETED:
                if subscription_charge.subscription.amount_unpaid <= 0:
                    subscription_charge.subscription.state = models.Subscription.STATE_ACTIVE
                    subscription_charge.subscription.save()
            elif instance.state == instance.STATE_FAILED:
                subscription_charge.subscription.state = models.Subscription.STATE_CANCELLED
                subscription_charge.subscription.save()
        else:
            if instance.state == instance.STATE_COMPLETED:
                mail_subscription_success(subscription_charge.subscription, instance.amount * -1)

                if subscription_charge.subscription.state == models.Subscription.STATE_PAST_DUE and \
                        subscription_charge.subscription.amount_unpaid <= 0:
                    subscription_charge.subscription.state = models.Subscription.STATE_ACTIVE
                    subscription_charge.subscription.save()
            elif instance.state == instance.STATE_FAILED:
                subscription_charge.failed_bill_attempts += 1
                subscription_charge.save()

                error_message = charge_state_2.last_error if charge_state_2 else None

                if subscription_charge.failed_bill_attempts >= SUBSCRIPTION_RETRY_ATTEMPTS:
                    if subscription_charge.subscription.state != models.Subscription.STATE_CANCELLED:
                        mail_subscription_cancelled(subscription_charge.subscription, instance.amount * -1, error_message)
                    subscription_charge.subscription.state = models.Subscription.STATE_CANCELLED
                    subscription_charge.subscription.save()
                else:
                    if subscription_charge.subscription.state != models.Subscription.STATE_CANCELLED:
                        mail_subscription_past_due(subscription_charge.subscription, instance.amount * -1, error_message)
                    subscription_charge.subscription.state = models.Subscription.STATE_PAST_DUE
                    subscription_charge.subscription.save()


@receiver(post_save, sender=models.ChargeState)
def send_charge_state_notif(sender, instance: models.ChargeState, **kwargs):
    if instance.notif_queue:
        status = instance.ledger_item.state

        if status == models.LedgerItem.STATE_PENDING:
            status = billing_pb2.ChargeStateNotification.PENDING
        elif status in (models.LedgerItem.STATE_PROCESSING, models.LedgerItem.STATE_PROCESSING_CANCELLABLE):
            status = billing_pb2.ChargeStateNotification.PROCESING
        elif status == models.LedgerItem.STATE_FAILED:
            status = billing_pb2.ChargeStateNotification.FAILED
        elif status == models.LedgerItem.STATE_COMPLETED:
            status = billing_pb2.ChargeStateNotification.COMPLETED
        else:
            status = billing_pb2.ChargeStateNotification.UNKNOWN

        msg = billing_pb2.ChargeStateNotification(
            charge_id=instance.id,
            account=instance.account.user.username if instance.account else "",
            state=status,
            last_error=google.protobuf.wrappers_pb2.StringValue(
                value=instance.last_error
            ) if instance.last_error else None
        )
        pika_connection = pika.BlockingConnection(parameters=pika_parameters)
        pika_channel = pika_connection.channel()
        pika_channel.basic_publish(
            exchange='',
            routing_key=instance.notif_queue,
            body=msg.SerializeToString()
        )
        pika_connection.close()


@receiver(post_save, sender=models.Subscription)
def send_subscription_notif(sender, instance: models.Subscription, **kwargs):
    if instance.plan.notif_queue:
        if instance.state == models.Subscription.STATE_PENDING:
            status = billing_pb2.SubscriptionNotification.PENDING
        elif instance.state == models.Subscription.STATE_ACTIVE:
            status = billing_pb2.SubscriptionNotification.ACTIVE
        elif instance.state == models.Subscription.STATE_PAST_DUE:
            status = billing_pb2.SubscriptionNotification.PAST_DUE
        elif instance.state == models.Subscription.STATE_CANCELLED:
            status = billing_pb2.SubscriptionNotification.CANCELLED
        else:
            status = billing_pb2.ChargeStateNotification.UNKNOWN

        msg = billing_pb2.SubscriptionNotification(
            subscription_id=instance.id,
            state=status,
        )
        pika_connection = pika.BlockingConnection(parameters=pika_parameters)
        pika_channel = pika_connection.channel()
        pika_channel.basic_publish(
            exchange='',
            routing_key=instance.plan.notif_queue,
            body=msg.SerializeToString()
        )
        pika_connection.close()


class ChargeError(Exception):
    def __init__(self, payment_ledger_item, message, must_reject=False):
        self.payment_ledger_item = payment_ledger_item
        self.message = message
        self.charge_state = None
        self.must_reject = True


class RequiresActionError(Exception):
    def __init__(self, payment_ledger_item, redirect_url=None):
        self.payment_ledger_item = payment_ledger_item
        self.redirect_url = redirect_url


class ChargeStateRequiresActionError(Exception):
    def __init__(self, charge_state, redirect_url=None):
        self.charge_state = charge_state
        self.redirect_url = redirect_url


def attempt_charge_off_session(charge_state):
    default_billing_address = models.AccountBillingAddress.objects \
        .filter(account=charge_state.account, deleted=False, default=True).first()

    if not default_billing_address and vat.need_billing_evidence():
        raise ChargeError(None, "No default billing address", must_reject=True)

    billing_address_country = default_billing_address.country_code.code.lower() \
        if default_billing_address else None
    order_total = charge_state.base_amount
    vat_rate = decimal.Decimal(0)
    selected_payment_method_type = None
    selected_payment_method_id = None
    taxable = not default_billing_address.vat_id

    if billing_address_country and taxable:
        country_vat_rate = vat.get_vat_rate(billing_address_country)
        if country_vat_rate is not None:
            vat_rate = country_vat_rate
            vat_charged = (order_total * country_vat_rate)
            order_total += vat_charged

    from_account_balance = min(charge_state.account.balance, order_total)
    left_to_be_paid = order_total - from_account_balance
    needs_payment = left_to_be_paid > 0
    account_evidence = {}
    payment_method_country = None
    currency = "gbp"

    if left_to_be_paid < decimal.Decimal(1):
        left_to_be_paid = decimal.Decimal(1)

    if needs_payment:
        if charge_state.account.default_stripe_payment_method_id:
            payment_method = stripe.PaymentMethod.retrieve(charge_state.account.default_stripe_payment_method_id)
            if payment_method["type"] == "sepa_debit":
                currency = "eur"
            selected_payment_method_type = "stripe_pm"
            selected_payment_method_id = payment_method
            payment_method_country = utils.country_from_stripe_payment_method(payment_method)
        elif charge_state.account.default_gc_mandate_id:
            mandate = apps.gocardless_client.mandates.get(charge_state.account.default_gc_mandate_id)

            if mandate.scheme == "ach":
                currency = "usd"
            elif mandate.scheme == "autogiro":
                currency = "sek"
            elif mandate.scheme == "becs":
                currency = "aud"
            elif mandate.scheme == "becs_nz":
                currency = "nzd"
            elif mandate.scheme == "betalingsservice":
                currency = "dkk"
            elif mandate.scheme == "pad":
                currency = "cad"
            elif mandate.scheme in ("sepa_core", "sepa_cor1"):
                currency = "eur"

            customer_bank_account = apps.gocardless_client.customer_bank_accounts.get(
                mandate.links.customer_bank_account)
            payment_method_country = customer_bank_account.country_code.lower()
            selected_payment_method_type = "gc_mandate"
            selected_payment_method_id = mandate

        if vat.need_billing_evidence():
            address_and_method_match = payment_method_country == billing_address_country
        else:
            address_and_method_match = True
    else:
        if vat.need_billing_evidence():
            account_evidence = find_account_evidence(charge_state.account, billing_address_country)
            address_and_method_match = account_evidence is not None
        else:
            address_and_method_match = True

    if not address_and_method_match:
        raise ChargeError(None, "Insufficient evidence for country of tax residency", must_reject=True)

    charge_state.vat_rate = vat_rate
    charge_state.country_code = billing_address_country
    charge_state.ready_to_complete = True
    charge_state.evidence_billing_address = default_billing_address
    charge_state.ledger_item.amount = -order_total
    charge_state.ledger_item.save()
    charge_state.save()

    if needs_payment:
        amount = models.ExchangeRate.get_rate("gbp", currency) * left_to_be_paid
        amount_int = int(round(amount * decimal.Decimal(100)))

        ledger_item_type = None
        if selected_payment_method_type == "stripe_pm":
            if selected_payment_method_id["type"] == "sepa_debit":
                ledger_item_type = models.LedgerItem.TYPE_SEPA
            else:
                ledger_item_type = models.LedgerItem.TYPE_CARD
        elif selected_payment_method_type == "gc_mandate":
            ledger_item_type = models.LedgerItem.TYPE_GOCARDLESS

        ledger_item = models.LedgerItem(
            account=charge_state.account,
            descriptor="Automatic top-up",
            amount=left_to_be_paid,
            type=ledger_item_type
        )

        if selected_payment_method_type == "stripe_pm":
            try:
                payment_intent = stripe.PaymentIntent.create(
                    amount=amount_int,
                    currency=currency,
                    customer=charge_state.account.get_stripe_id(),
                    description='Top-up',
                    receipt_email=charge_state.account.user.email,
                    statement_descriptor_suffix="Top-up",
                    payment_method=selected_payment_method_id["id"],
                    payment_method_types=[selected_payment_method_id["type"]],
                    confirm=True,
                    off_session=True,
                )
            except (stripe.error.CardError, stripe.error.InvalidRequestError) as e:
                if isinstance(e, stripe.error.InvalidRequestError):
                    message = "Payment failed"
                else:
                    err = e.error
                    message = err.message
                    ledger_item.type_id = err.payment_intent['id']
                ledger_item.state = ledger_item.STATE_FAILED
                ledger_item.save()
                raise ChargeError(ledger_item, message, must_reject=False)

            known_payment_method, _ = models.KnownStripePaymentMethod.objects.update_or_create(
                account=charge_state.account, method_id=payment_intent["payment_method"],
                defaults={
                    "country_code": payment_method_country
                }
            )

            ledger_item.save()
            ledger_item.type_id = payment_intent['id']
            charge_state.payment_ledger_item = ledger_item
            charge_state.evidence_stripe_pm = known_payment_method
            charge_state.save()
            ledger_item.save()
            update_from_payment_intent(payment_intent, ledger_item)
        elif selected_payment_method_type == "gc_mandate":
            payment = apps.gocardless_client.payments.create(params={
                "amount": amount_int,
                "currency": currency.upper(),
                "description": "Top up",
                "retry_if_possible": False,
                "links": {
                    "mandate": selected_payment_method_id.id
                }
            })
            if selected_payment_method_id.scheme == "ach":
                charge_state.evidence_ach_mandate = models.ACHMandate.sync_mandate(
                     selected_payment_method_id.id, charge_state.account)
            elif selected_payment_method_id.scheme == "autogiro":
                charge_state.evidence_autogiro_mandate = models.AutogiroMandate.sync_mandate(
                    selected_payment_method_id.id, charge_state.account)
            elif selected_payment_method_id.scheme == "bacs":
                charge_state.evidence_gc_bacs_mandate = models.GCBACSMandate.sync_mandate(
                    selected_payment_method_id.id, charge_state.account)
            elif selected_payment_method_id.scheme == "becs":
                charge_state.evidence_becs_mandate = models.BECSMandate.sync_mandate(
                    selected_payment_method_id.id, charge_state.account)
            elif selected_payment_method_id.scheme == "becs_nz":
                charge_state.evidence_becs_nz_mandate = models.BECSNZMandate.sync_mandate(
                    selected_payment_method_id.id, charge_state.account)
            elif selected_payment_method_id.scheme == "betalingsservice":
                charge_state.evidence_betalingsservice_mandate = models.BetalingsserviceMandate.sync_mandate(
                    selected_payment_method_id.id, charge_state.account)
            elif selected_payment_method_id.scheme == "pad":
                charge_state.evidence_pad_mandate = models.PADMandate.sync_mandate(
                    selected_payment_method_id.id, charge_state.account)
            elif selected_payment_method_id.scheme in ("sepa_core", "sepa_cor1"):
                charge_state.evidence_gc_sepa_mandate = models.GCSEPAMandate.sync_mandate(
                    selected_payment_method_id.id, charge_state.account)

            ledger_item.save()
            charge_state.payment_ledger_item = ledger_item
            charge_state.save()
            ledger_item.type_id = payment.id
            ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
            ledger_item.save()
    else:
        for key in account_evidence:
            setattr(charge_state, key, account_evidence[key])
        charge_state.ledger_item.state = models.LedgerItem.STATE_COMPLETED
        charge_state.ledger_item.save()
        charge_state.save()


def charge_account(account: models.Account, amount: decimal.Decimal, descriptor: str, type_id: str, can_reject=True,
                   off_session=True, return_uri=None, notif_queue=None, supports_delayed=False):
    ledger_item = models.LedgerItem(
        account=account,
        descriptor=descriptor,
        amount=-amount,
        type=models.LedgerItem.TYPE_CHARGE,
        type_id=type_id,
        timestamp=timezone.now(),
    )
    charge_state = models.ChargeState(
        account=account,
        ledger_item=ledger_item,
        return_uri=return_uri,
        notif_queue=notif_queue,
        base_amount=amount,
        can_reject=can_reject
    )

    if not account:
        if off_session:
            raise ChargeError(None, "Account does not exist")

        ledger_item.save()
        charge_state.save()
        raise ChargeStateRequiresActionError(
            charge_state, settings.EXTERNAL_URL_BASE + reverse('complete_order', args=(charge_state.id,))
        )

    if off_session:
        ledger_item.save()
        charge_state.save()
        try:
            attempt_charge_off_session(charge_state)
            return charge_state
        except ChargeError as e:
            if not e.must_reject and not can_reject:
                ledger_item.state = models.LedgerItem.STATE_COMPLETED

            charge_state.last_error = e.message
            charge_state.save()
            e.charge_state = charge_state
            if supports_delayed:
                raise ChargeStateRequiresActionError(
                    charge_state, settings.EXTERNAL_URL_BASE + reverse('complete_order', args=(charge_state.id,))
                )
            else:
                ledger_item.state = ledger_item.STATE_FAILED
            ledger_item.save()
            raise e
    else:
        ledger_item.save()
        charge_state.save()
        raise ChargeStateRequiresActionError(
            charge_state, settings.EXTERNAL_URL_BASE + reverse('complete_order', args=(charge_state.id,))
        )


def update_from_payment_intent(payment_intent, ledger_item: models.LedgerItem = None):
    ledger_item = models.LedgerItem.objects.filter(
        Q(type=models.LedgerItem.TYPE_CARD) | Q(type=models.LedgerItem.TYPE_SEPA) |
        Q(type=models.LedgerItem.TYPE_SOFORT) | Q(type=models.LedgerItem.TYPE_GIROPAY) |
        Q(type=models.LedgerItem.TYPE_BANCONTACT) | Q(type=models.LedgerItem.TYPE_EPS) |
        Q(type=models.LedgerItem.TYPE_IDEAL) | Q(type=models.LedgerItem.TYPE_P24)
    ).filter(type_id=payment_intent['id']).first() if not ledger_item else ledger_item

    if not ledger_item:
        return

    account = ledger_item.account if ledger_item else \
        models.Account.objects.filter(stripe_customer_id=payment_intent["customer"]).first()

    for charge in payment_intent["charges"]["data"]:
        if charge["payment_method_details"]["type"] == "sepa_debit":
            models.SEPAMandate.sync_mandate(
                charge["payment_method_details"]["sepa_debit"]["mandate"],
                account
            )
        elif charge["payment_method_details"]["type"] == "sofort":
            if "generated_sepa_debit_mandate" in charge["payment_method_details"]["sofort"]:
                models.SEPAMandate.sync_mandate(
                    charge["payment_method_details"]["sofort"]["generated_sepa_debit_mandate"],
                    account
                )
        elif charge["payment_method_details"]["type"] == "bancontact":
            if "generated_sepa_debit_mandate" in charge["payment_method_details"]["bancontact"]:
                models.SEPAMandate.sync_mandate(
                    charge["payment_method_details"]["bancontact"]["generated_sepa_debit_mandate"],
                    account
                )
        elif charge["payment_method_details"]["type"] == "ideal":
            if "generated_sepa_debit_mandate" in charge["payment_method_details"]["ideal"]:
                models.SEPAMandate.sync_mandate(
                    charge["payment_method_details"]["ideal"]["generated_sepa_debit_mandate"],
                    ledger_item.account if ledger_item else
                    models.Account.objects.filter(stripe_customer_id=payment_intent["customer"]).first()
                )

    if payment_intent["status"] == "succeeded":
        amount = decimal.Decimal(payment_intent["amount_received"]) / decimal.Decimal(100)
        amount = models.ExchangeRate.get_rate(payment_intent['currency'], 'gbp') * amount
        ledger_item.amount = amount
        ledger_item.state = models.LedgerItem.STATE_COMPLETED
        ledger_item.save()
        if account:
            payment_method = stripe.PaymentMethod.retrieve(payment_intent["payment_method"])
            payment_method_country = utils.country_from_stripe_payment_method(payment_method)
            known_payment_method, _ = models.KnownStripePaymentMethod.objects.update_or_create(
                account=account, method_id=payment_method["id"],
                defaults={
                    "country_code": payment_method_country
                }
            )
            if ledger_item.charge_state_payment:
                ledger_item.charge_state_payment.evidence_stripe_pm = known_payment_method
                ledger_item.charge_state_payment.save()
    elif payment_intent["status"] == "processing":
        ledger_item.state = models.LedgerItem.STATE_PROCESSING
        ledger_item.save()
    elif payment_intent["status"] == "requires_action":
        ledger_item.state = models.LedgerItem.STATE_PENDING
        ledger_item.save()
    elif (payment_intent["status"] == "requires_payment_method" and payment_intent["last_payment_error"]) \
            or payment_intent["status"] == "canceled":
        ledger_item.state = models.LedgerItem.STATE_FAILED
        try:
            charge_state = ledger_item.charge_state_payment
        except django.core.exceptions.ObjectDoesNotExist:
            return
        error = payment_intent["last_payment_error"]["message"] if payment_intent["last_payment_error"] \
            else "Payment failed"
        charge_state.last_error = error
        charge_state.save()
        ledger_item.save()


def update_from_source(source, ledger_item=None):
    ledger_item = models.LedgerItem.objects.filter(
        type=models.LedgerItem.TYPE_SOURCES,
        type_id=source['id']
    ).first() if not ledger_item else ledger_item

    if not ledger_item:
        return

    if source["status"] == "chargeable":
        charge = stripe.Charge.create(
            amount=source['amount'],
            currency=source['currency'],
            source=source['id'],
            customer=ledger_item.account.get_stripe_id(),
            receipt_email=ledger_item.account.user.email,
        )
        ledger_item.type_id = charge['id']
        ledger_item.type = models.LedgerItem.TYPE_CHARGES
        ledger_item.state = models.LedgerItem.STATE_PROCESSING
        ledger_item.save()
        update_from_charge(charge, ledger_item)
    elif source["status"] in ("failed", "canceled"):
        ledger_item.state = models.LedgerItem.STATE_FAILED
        ledger_item.save()


def update_from_charge(charge, ledger_item=None):
    if charge["payment_method_details"]["type"] == "sepa_debit":
        models.SEPAMandate.sync_mandate(
            charge["payment_method_details"]["sepa_debit"]["mandate"],
            models.Account.objects.filter(stripe_customer_id=charge["customer"]).first()
        )
    elif charge["payment_method_details"]["type"] == "sofort":
        if "generated_sepa_debit_mandate" in charge["payment_method_details"]["sofort"]:
            models.SEPAMandate.sync_mandate(
                charge["payment_method_details"]["sofort"]["generated_sepa_debit_mandate"],
                models.Account.objects.filter(stripe_customer_id=charge["customer"]).first()
            )
    elif charge["payment_method_details"]["type"] == "bancontact":
        if "generated_sepa_debit_mandate" in charge["payment_method_details"]["bancontact"]:
            models.SEPAMandate.sync_mandate(
                charge["payment_method_details"]["bancontact"]["generated_sepa_debit_mandate"],
                models.Account.objects.filter(stripe_customer_id=charge["customer"]).first()
            )
    elif charge["payment_method_details"]["type"] == "ideal":
        if "generated_sepa_debit_mandate" in charge["payment_method_details"]["ideal"]:
            models.SEPAMandate.sync_mandate(
                charge["payment_method_details"]["ideal"]["generated_sepa_debit_mandate"],
                models.Account.objects.filter(stripe_customer_id=charge["customer"]).first()
            )

    ledger_item = models.LedgerItem.objects.filter(
        type=models.LedgerItem.TYPE_CHARGES,
        type_id=charge['id']
    ).first() if not ledger_item else ledger_item

    if charge["refunded"]:
        if not ledger_item and charge['payment_intent']:
            ledger_item = models.LedgerItem.objects.filter(
                Q(type=models.LedgerItem.TYPE_CARD) | Q(type=models.LedgerItem.TYPE_SEPA) |
                Q(type=models.LedgerItem.TYPE_SOFORT) | Q(type=models.LedgerItem.TYPE_GIROPAY) |
                Q(type=models.LedgerItem.TYPE_BANCONTACT) | Q(type=models.LedgerItem.TYPE_EPS) |
                Q(type=models.LedgerItem.TYPE_IDEAL) | Q(type=models.LedgerItem.TYPE_P24)
            ).filter(
                type_id=charge['payment_intent']
            ).first() if not ledger_item else ledger_item

        if ledger_item:
            reversal_ledger_item = models.LedgerItem.objects.filter(
                type=models.LedgerItem.TYPE_CHARGE,
                type_id=ledger_item.id,
                is_reversal=True
            ).first()  # type: models.LedgerItem
            if not reversal_ledger_item:
                new_ledger_item = models.LedgerItem(
                    account=ledger_item.account,
                    descriptor=ledger_item.descriptor,
                    amount=-(decimal.Decimal(charge["amount_refunded"]) / decimal.Decimal(100)),
                    type=models.LedgerItem.TYPE_CHARGE,
                    type_id=ledger_item.id,
                    timestamp=timezone.now(),
                    state=ledger_item.STATE_COMPLETED,
                    is_reversal=True
                )
                new_ledger_item.save()

    if not ledger_item:
        return

    if charge["status"] == "succeeded":
        ledger_item.state = models.LedgerItem.STATE_COMPLETED
        ledger_item.save()
    elif charge["status"] == "pending":
        ledger_item.state = models.LedgerItem.STATE_PROCESSING
        ledger_item.save()
    elif charge["status"] == "failed":
        ledger_item.state = models.LedgerItem.STATE_FAILED
        ledger_item.save()


def update_from_checkout_session(session, ledger_item=None):
    ledger_item = models.LedgerItem.objects.filter(
        type=models.LedgerItem.TYPE_CHECKOUT,
        type_id=session['id']
    ).first() if not ledger_item else ledger_item

    if ledger_item:
        ledger_item.state = models.LedgerItem.STATE_PROCESSING
        ledger_item.save()

    if session["mode"] == "payment":
        payment_intent = stripe.PaymentIntent.retrieve(session["payment_intent"])
        update_from_payment_intent(payment_intent, ledger_item)
    elif session["mode"] == "setup":
        setup_intent = stripe.SetupIntent.retrieve(session["setup_intent"])
        if "bacs_debit" in session["payment_method_types"]:
            models.BACSMandate.sync_mandate(
                setup_intent["mandate"], ledger_item.account if ledger_item else
                models.Account.objects.filter(stripe_customer_id=setup_intent["customer"]).first()
            )
        if "sepa_debit" in session["payment_method_types"]:
            models.SEPAMandate.sync_mandate(
                setup_intent["mandate"], ledger_item.account if ledger_item else
                models.Account.objects.filter(stripe_customer_id=setup_intent["customer"]).first()
            )

        if setup_intent["status"] == "succeeded":
            account = ledger_item.account if ledger_item else \
                models.Account.objects.filter(stripe_customer_id=setup_intent["customer"]).first()
            payment_method = stripe.PaymentMethod.retrieve(setup_intent["payment_method"])
            payment_method_country = utils.country_from_stripe_payment_method(payment_method)
            models.KnownStripePaymentMethod.objects.update_or_create(
                account=account, method_id=payment_method["id"],
                defaults={
                    "country_code": payment_method_country
                }
            )


def setup_intent_succeeded(setup_intent):
    if "sepa_debit" in setup_intent["payment_method_types"]:
        models.SEPAMandate.sync_mandate(
            setup_intent["mandate"],
            models.Account.objects.filter(stripe_customer_id=setup_intent["customer"]).first()
        )


def mandate_update(mandate):
    payment_method = stripe.PaymentMethod.retrieve(mandate["payment_method"])
    account = models.Account.objects.filter(stripe_customer_id=payment_method["customer"]).first()
    if mandate["payment_method_details"]["type"] == "bacs_debit":
        models.BACSMandate.sync_mandate(
            mandate["id"], account
        )
    elif mandate["payment_method_details"]["type"] == "sepa_debit":
        models.SEPAMandate.sync_mandate(
            mandate["id"], account
        )


def sync_payment_methods(account: models.Account):
    cards = list(stripe.PaymentMethod.list(
        customer=account.get_stripe_id(),
        type="card"
    ).auto_paging_iter())
    for card in cards:
        models.KnownStripePaymentMethod.objects.update_or_create(
            account=account, method_id=card["id"],
            defaults={
                "country_code": utils.country_from_stripe_payment_method(card)
            }
        )


def find_account_evidence(account: models.Account, country_code):
    evidence = {}
    possible_bank_accounts = models.KnownBankAccount.objects.filter(
        country_code=country_code,
        account=account
    )
    if possible_bank_accounts.count():
        evidence["evidence_bank_account"] = possible_bank_accounts.first()

    if country_code == "us":
        possible_ach_mandate = models.ACHMandate.objects.filter(
            account=account,
            active=True
        )
        if possible_ach_mandate.count():
            evidence["evidence_ach_mandate"] = possible_ach_mandate.first()
    elif country_code == "se":
        possible_autogiro_mandate = models.AutogiroMandate.objects.filter(
            account=account,
            active=True
        )
        if possible_autogiro_mandate.count():
            evidence["evidence_autogiro_mandate"] = possible_autogiro_mandate.first()
    elif country_code == "gb":
        possible_bacs_mandate = models.BACSMandate.objects.filter(
            account=account,
            active=True
        )
        if possible_bacs_mandate.count():
            evidence["evidence_bacs_mandate"] = possible_bacs_mandate.first()
        possible_gc_bacs_mandate = models.GCBACSMandate.objects.filter(
            account=account,
            active=True
        )
        if possible_gc_bacs_mandate.count():
            evidence["evidence_gc_bacs_mandate"] = possible_gc_bacs_mandate.first()
    elif country_code == "au":
        possible_becs_mandate = models.BECSMandate.objects.filter(
            account=account,
            active=True
        )
        if possible_becs_mandate.count():
            evidence["evidence_becs_mandate"] = possible_becs_mandate.first()
    elif country_code == "nz":
        possible_becs_nz_mandate = models.BECSNZMandate.objects.filter(
            account=account,
            active=True
        )
        if possible_becs_nz_mandate.count():
            evidence["evidence_becs_nz_mandate"] = possible_becs_nz_mandate.first()
    elif country_code == "dk":
        possible_betalingsservice_mandate = models.BetalingsserviceMandate.objects.filter(
            account=account,
            active=True
        )
        if possible_betalingsservice_mandate.count():
            evidence["evidence_betalingsservice_mandate"] = possible_betalingsservice_mandate.first()
    elif country_code == "ca":
        possible_pad_mandate = models.PADMandate.objects.filter(
            account=account,
            active=True
        )
        if possible_pad_mandate.count():
            evidence["evidence_pad_mandate"] = possible_pad_mandate.first()

    possible_sepa_mandates = models.SEPAMandate.objects.filter(
        account=account,
        active=True
    )
    for possible_sepa_mandate in possible_sepa_mandates:
        payment_method = stripe.PaymentMethod.retrieve(possible_sepa_mandate.payment_method)
        payment_method_country = utils.country_from_stripe_payment_method(payment_method)
        if payment_method_country == country_code:
            evidence["evidence_sepa_mandate"] = possible_sepa_mandate
            break

    possible_gc_sepa_mandates = models.GCSEPAMandate.objects.filter(
        account=account,
        active=True
    )
    for possible_gc_sepa_mandate in possible_gc_sepa_mandates:
        mandate = apps.gocardless_client.mandates.get(possible_gc_sepa_mandate.mandate_id)
        customer_bank_account = apps.gocardless_client.customer_bank_accounts.get(mandate.links.customer_bank_account)
        payment_method_country = customer_bank_account.country_code.lower()
        if payment_method_country == country_code:
            evidence["evidence_gc_sepa_mandate"] = possible_gc_sepa_mandate
            break

    possible_stripe_pms = models.KnownStripePaymentMethod.objects.filter(
        country_code=country_code,
        account=account
    )
    if not possible_stripe_pms.count() and not bool(evidence):
        sync_payment_methods(account)
        possible_stripe_pms = models.KnownStripePaymentMethod.objects.filter(
            country_code=country_code,
            account=account
        )
    if possible_stripe_pms.count():
        evidence["evidence_stripe_pm"] = possible_stripe_pms.first()

    return evidence if bool(evidence) else None
