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


def get_state_name_and_emoji(state, new=False) -> (str, str):
    if state == models.LedgerItem.STATE_PENDING:
        return "pending", "⌛"
    elif state in (models.LedgerItem.STATE_PROCESSING, models.LedgerItem.STATE_PROCESSING_CANCELLABLE):
        return "processing", "📇"
    elif state == models.LedgerItem.STATE_FAILED:
        return "failed", "🙅"
    elif state == models.LedgerItem.STATE_COMPLETED and not new:
        return "completed", None


def mail_notif(ledger_item: models.LedgerItem):
    try:
        charge_state = ledger_item.charge_state
        is_payment_item = False
    except django.core.exceptions.ObjectDoesNotExist:
        try:
            charge_state = ledger_item.charge_state_payment
            is_payment_item = True
        except django.core.exceptions.ObjectDoesNotExist:
            charge_state = None
            is_payment_item = False

    charge_state_url = None
    charge_error = None
    if charge_state and (charge_state.ledger_item.state != charge_state.ledger_item.STATE_FAILED or is_payment_item):
        charge_state_url = settings.EXTERNAL_URL_BASE + reverse('complete_order', args=(charge_state.id,))

    if charge_state and charge_state.last_error and not is_payment_item:
        charge_error = charge_state.last_error

    state_name, _ = get_state_name_and_emoji(ledger_item.state)

    context = {
        "name": ledger_item.account.user.first_name,
        "item": ledger_item,
        "charge_state_url": charge_state_url,
        "charge_state_error": charge_error,
    }
    html_content = render_to_string("billing_email/billing_notif.html", context)
    txt_content = render_to_string("billing_email/billing_notif.txt", context)

    subject = f"{ledger_item.descriptor}: {state_name}"
    email_msg = EmailMultiAlternatives(
        subject=subject,
        body=txt_content,
        to=[ledger_item.account.user.email],
        bcc=['email-log@as207960.net'],
        reply_to=['hello@glauca.digital']
    )
    email_msg.attach_alternative(html_content, "text/html")
    email_msg.send()


@as_thread
def mail_subscription_success(subscription: models.Subscription, item: models.LedgerItem):
    try:
        charge_state = item.charge_state
    except django.core.exceptions.ObjectDoesNotExist:
        charge_state = None

    context = {
        "name": subscription.account.user.first_name,
        "plan_name": subscription.plan.name,
        "item": item,
        "charge_state": charge_state
    }
    html_content = render_to_string("billing_email/billing_success.html", context)
    txt_content = render_to_string("billing_email/billing_success.txt", context)

    email_msg = EmailMultiAlternatives(
        subject='Subscription payment successful',
        body=txt_content,
        to=[subscription.account.user.email],
        bcc=['email-log@as207960.net'],
        reply_to=['hello@glauca.digital']
    )
    email_msg.attach_alternative(html_content, "text/html")
    email_msg.send()


@as_thread
def mail_subscription_past_due(subscription: models.Subscription, item: models.LedgerItem):
    try:
        charge_state = item.charge_state
    except django.core.exceptions.ObjectDoesNotExist:
        charge_state = None

    context = {
        "name": subscription.account.user.first_name,
        "plan_name": subscription.plan.name,
        "item": item,
        "charge_state": charge_state
    }
    html_content = render_to_string("billing_email/billing_past_due.html", context)
    txt_content = render_to_string("billing_email/billing_past_due.txt", context)

    email_msg = EmailMultiAlternatives(
        subject='Subscription payment failed',
        body=txt_content,
        to=[subscription.account.user.email],
        bcc=['email-log@as207960.net'],
        reply_to=['hello@glauca.digital']
    )
    email_msg.attach_alternative(html_content, "text/html")
    email_msg.send()


@as_thread
def mail_subscription_cancelled(subscription: models.Subscription, item: models.LedgerItem):
    try:
        charge_state = item.charge_state
    except django.core.exceptions.ObjectDoesNotExist:
        charge_state = None

    context = {
        "name": subscription.account.user.first_name,
        "plan_name": subscription.plan.name,
        "item": item,
        "charge_state": charge_state
    }
    html_content = render_to_string("billing_email/billing_cancelled.html", context)
    txt_content = render_to_string("billing_email/billing_cancelled.txt", context)

    email_msg = EmailMultiAlternatives(
        subject='Subscription cancelled',
        body=txt_content,
        to=[subscription.account.user.email],
        bcc=['email-log@as207960.net'],
        reply_to=['hello@glauca.digital']
    )
    email_msg.attach_alternative(html_content, "text/html")
    email_msg.send()


@as_thread
def alert_account(account: models.Account, ledger_item: models.LedgerItem, new=False, mail=True):
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

    extra, new_emoji = get_state_name_and_emoji(ledger_item.state, new)
    if new_emoji:
        emoji = new_emoji

    message = f"{emoji} {body}"
    if extra:
        message += f": {extra}"

    if ledger_item.amount != 0 and mail:
        mail_notif(ledger_item)

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


# @receiver(pre_save, sender=models.LedgerItem)
# def send_item_notif(sender, instance: models.LedgerItem, **kwargs):
    # old_instance = models.LedgerItem.objects.filter(id=instance.id).first()
    # if not old_instance:
    #     instance.last_state_change_timestamp = timezone.now()
    #     alert_account(instance.account, instance, new=True)
    # elif old_instance.state != instance.state:
    #     instance.last_state_change_timestamp = timezone.now()
    #     alert_account(instance.account, instance)

def fail_payment(ledger_item):
    if ledger_item.state not in (ledger_item.STATE_PENDING, ledger_item.STATE_PROCESSING_CANCELLABLE):
        return

    if ledger_item.type not in (
            ledger_item.TYPE_CARD, ledger_item.TYPE_BACS, ledger_item.TYPE_SOURCES, ledger_item.TYPE_CHECKOUT,
            ledger_item.TYPE_SEPA, ledger_item.TYPE_SOFORT, ledger_item.TYPE_GIROPAY, ledger_item.TYPE_BANCONTACT,
            ledger_item.TYPE_EPS, ledger_item.TYPE_IDEAL, ledger_item.TYPE_P24, ledger_item.TYPE_GOCARDLESS,
            ledger_item.TYPE_STRIPE_BACS
    ):
        return

    if ledger_item.type in (
            ledger_item.TYPE_CARD, ledger_item.TYPE_SEPA, ledger_item.TYPE_SOFORT, ledger_item.TYPE_GIROPAY,
            ledger_item.TYPE_BANCONTACT, ledger_item.TYPE_EPS, ledger_item.TYPE_IDEAL, ledger_item.TYPE_P24,
            ledger_item.TYPE_STRIPE_BACS
    ):
        payment_intent = stripe.PaymentIntent.retrieve(ledger_item.type_id)
        if payment_intent["status"] == "succeeded":
            ledger_item.state = ledger_item.STATE_COMPLETED
            ledger_item.save()
            return
        stripe.PaymentIntent.cancel(ledger_item.type_id)
    elif ledger_item.type == ledger_item.TYPE_CHECKOUT:
        session = stripe.checkout.Session.retrieve(ledger_item.type_id)
        stripe.PaymentIntent.cancel(session["payment_intent"])
    elif ledger_item.type == ledger_item.TYPE_GOCARDLESS:
        apps.gocardless_client.payments.cancel(ledger_item.type_id)

    ledger_item.delete()


def try_update_charge_state(instance: models.LedgerItem, mail=True, force_mail=False):
    try:
        as_thread(flux.send_charge_state_notif)(instance.charge_state)
    except django.core.exceptions.ObjectDoesNotExist:
        pass
    try:
        as_thread(flux.send_charge_state_notif)(instance.charge_state_payment)
    except django.core.exceptions.ObjectDoesNotExist:
        pass

    subscription_mail_sent = False

    if instance.payment_charge_state and \
            instance.state == instance.STATE_COMPLETED:
        instance.payment_charge_state.payment_ledger_item = instance
        for item in instance.payment_charge_state.payment_items.exclude(id=instance.id):
            fail_payment(item)

        instance.payment_charge_state.save()

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
    if charge_state:
        send_charge_state_notif(charge_state)

    try:
        charge_state_2 = instance.charge_state
    except django.core.exceptions.ObjectDoesNotExist:
        charge_state_2 = None

    if charge_state_2:
        send_charge_state_notif(charge_state_2)

    subscription_charge = instance.subscription_charge
    if subscription_charge and subscription_charge.subscription.state != models.Subscription.STATE_CANCELLED:
        if not subscription_charge.subscription.account:
            subscription_charge.subscription.account = instance.account
            subscription_charge.save()
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
                if instance.amount != 0:
                    mail_subscription_success(subscription_charge.subscription, instance)
                    subscription_mail_sent = True

                if subscription_charge.subscription.state == models.Subscription.STATE_PAST_DUE and \
                        subscription_charge.subscription.amount_unpaid <= 0:
                    subscription_charge.subscription.state = models.Subscription.STATE_ACTIVE
                    subscription_charge.subscription.save()
            elif instance.state == instance.STATE_FAILED:
                if subscription_charge.failed_bill_attempts >= SUBSCRIPTION_RETRY_ATTEMPTS:
                    mail_subscription_cancelled(subscription_charge.subscription, instance)
                    subscription_mail_sent = True
                    subscription_charge.subscription.state = models.Subscription.STATE_CANCELLED
                    subscription_charge.subscription.save()
                else:
                    mail_subscription_past_due(subscription_charge.subscription, instance)
                    subscription_mail_sent = True
                    subscription_charge.subscription.state = models.Subscription.STATE_PAST_DUE
                    subscription_charge.subscription.save()

    if instance.state != instance.original_state or force_mail:
        instance.original_state = instance.state
        if mail:
            alert_account(instance.account, instance, mail=not subscription_mail_sent)


def send_charge_state_notif(instance: models.ChargeState):
    if instance.notif_queue:
        status = instance.ledger_item.state

        if status == models.LedgerItem.STATE_PENDING:
            status = billing_pb2.ChargeStateNotification.PENDING
        elif status in (models.LedgerItem.STATE_PROCESSING, models.LedgerItem.STATE_PROCESSING_CANCELLABLE):
            status = billing_pb2.ChargeStateNotification.PROCESSING
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


@receiver(post_save, sender=models.ChargeState)
def send_charge_state_notif_receiver(sender, instance: models.ChargeState, **kwargs):
    send_charge_state_notif(instance)


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
    account = charge_state.account  # type: models.Account

    from_account_balance = min(charge_state.account.balance, -charge_state.ledger_item.amount)
    left_to_be_paid = -(charge_state.ledger_item.amount + from_account_balance)
    needs_payment = left_to_be_paid > 0

    if left_to_be_paid < decimal.Decimal(1):
        left_to_be_paid = decimal.Decimal(1)

    charged_amount = left_to_be_paid

    vat_rate = decimal.Decimal(0)
    selected_currency = "gbp"
    billing_address_country = account.billing_address.country_code.code.lower() if account.billing_address else None
    selected_payment_method_type = None
    selected_payment_method_id = None
    climate_contribution = False

    if needs_payment:
        if not account.billing_address:
            raise ChargeError(None, "No default billing address", must_reject=True)

        if account.taxable:
            country_vat_rate = vat.get_vat_rate(billing_address_country, account.billing_address.postal_code)
            if country_vat_rate is not None:
                vat_rate = country_vat_rate
                vat_charged = (left_to_be_paid * country_vat_rate)
                charged_amount += vat_charged

        can_sell, can_sell_reason = account.can_sell
        if not can_sell:
            raise ChargeError(None, can_sell_reason, must_reject=True)

        if charge_state.account.default_stripe_payment_method_id:
            payment_method = stripe.PaymentMethod.retrieve(charge_state.account.default_stripe_payment_method_id)
            method_country = utils.country_from_stripe_payment_method(payment_method)
            if method_country == billing_address_country or not account.taxable:
                if payment_method["type"] == "card":
                    if selected_currency not in ['gbp', 'eur', 'usd']:
                        selected_currency = 'gbp'
            else:
                raise ChargeError(None, "Insufficient evidence for country of tax residency", must_reject=True)
            climate_contribution = True
            selected_payment_method_type = "stripe_pm"
            selected_payment_method_id = charge_state.account.default_stripe_payment_method_id
        elif charge_state.account.default_sepa_mandate:
            payment_method = stripe.PaymentMethod.retrieve(charge_state.account.default_sepa_mandate.payment_method)
            method_country = utils.country_from_stripe_payment_method(payment_method)
            if method_country == billing_address_country or not account.taxable:
                selected_currency = 'eur'
            else:
                raise ChargeError(None, "Insufficient evidence for country of tax residency", must_reject=True)
            climate_contribution = True
            selected_payment_method_type = "sepa_mandate_stripe"
            selected_payment_method_id = charge_state.account.default_sepa_mandate.payment_method
        elif charge_state.account.default_bacs_mandate:
            payment_method = stripe.PaymentMethod.retrieve(charge_state.account.default_bacs_mandate.payment_method)
            method_country = utils.country_from_stripe_payment_method(payment_method)
            if method_country == billing_address_country or not account.taxable:
                selected_currency = 'gbp'
            else:
                raise ChargeError(None, "Insufficient evidence for country of tax residency", must_reject=True)
            climate_contribution = True
            selected_payment_method_type = "bacs_mandate_stripe"
            selected_payment_method_id = charge_state.account.default_bacs_mandate.payment_method
        elif charge_state.account.default_ach_mandate:
            if billing_address_country == "us" or not account.taxable:
                selected_currency = 'usd'
            else:
                raise ChargeError(None, "Insufficient evidence for country of tax residency", must_reject=True)
            selected_payment_method_type = "ach_mandate_gc"
            selected_payment_method_id = charge_state.account.default_ach_mandate
        elif charge_state.account.default_autogiro_mandate:
            if billing_address_country == "se" or not account.taxable:
                selected_currency = 'sek'
            else:
                raise ChargeError(None, "Insufficient evidence for country of tax residency", must_reject=True)
            selected_payment_method_type = "autogiro_mandate_gc"
            selected_payment_method_id = charge_state.account.default_autogiro_mandate
        elif charge_state.account.default_gc_bacs_mandate:
            if billing_address_country == "gb" or not account.taxable:
                selected_currency = 'gbp'
            else:
                raise ChargeError(None, "Insufficient evidence for country of tax residency", must_reject=True)
            selected_payment_method_type = "bacs_mandate_gc"
            selected_payment_method_id = charge_state.account.default_gc_bacs_mandate
        elif charge_state.account.default_becs_mandate:
            if billing_address_country == "au" or not account.taxable:
                selected_currency = 'aud'
            else:
                raise ChargeError(None, "Insufficient evidence for country of tax residency", must_reject=True)
            selected_payment_method_type = "becs_mandate_gc"
            selected_payment_method_id = charge_state.account.default_becs_mandate
        elif charge_state.account.default_becs_nz_mandate:
            if billing_address_country == "nz" or not account.taxable:
                selected_currency = 'nzd'
            else:
                raise ChargeError(None, "Insufficient evidence for country of tax residency", must_reject=True)
            selected_payment_method_type = "becs_nz_mandate_gc"
            selected_payment_method_id = charge_state.account.default_becs_nz_mandate
        elif charge_state.account.default_betalingsservice_mandate:
            if billing_address_country == "dk" or not account.taxable:
                selected_currency = 'dkk'
            else:
                raise ChargeError(None, "Insufficient evidence for country of tax residency", must_reject=True)
            selected_payment_method_type = "betalingsservice_mandate_gc"
            selected_payment_method_id = charge_state.account.default_betalingsservice_mandate
        elif charge_state.account.default_pad_mandate:
            if billing_address_country == "ca" or not account.taxable:
                selected_currency = 'cad'
            else:
                raise ChargeError(None, "Insufficient evidence for country of tax residency", must_reject=True)
            selected_payment_method_type = "pad_mandate_gc"
            selected_payment_method_id = charge_state.account.default_pad_mandate
        elif charge_state.account.default_gc_sepa_mandate:
            mandate = apps.gocardless_client.mandates.get(charge_state.account.default_gc_sepa_mandate.mandate_id)
            bank_account = apps.gocardless_client.customer_bank_accounts.get(mandate.links.customer_bank_account)
            if bank_account.country_code.lower() == billing_address_country or not account.taxable:
                selected_currency = 'eur'
            else:
                raise ChargeError(None, "Insufficient evidence for country of tax residency", must_reject=True)
            selected_payment_method_type = "sepa_mandate_gc"
            selected_payment_method_id = charge_state.account.default_gc_sepa_mandate
        else:
            raise ChargeError(None, "No payment method on file", must_reject=False)

    charge_state.ready_to_complete = True
    charge_state.save()

    if not selected_currency:
        raise ChargeError(None, "No mutually supported currency", must_reject=False)

    if needs_payment:
        ledger_item = models.LedgerItem(
            account=account,
            amount=left_to_be_paid,
            vat_rate=vat_rate,
            country_code=billing_address_country,
            evidence_billing_address=account.billing_address,
            charged_amount=charged_amount,
            eur_exchange_rate=models.ExchangeRate.get_rate("gbp", "eur"),
            payment_charge_state=charge_state,
        )
        if climate_contribution and settings.STRIPE_CLIMATE:
            ledger_item.stripe_climate_contribution = charged_amount * decimal.Decimal(settings.STRIPE_CLIMATE_RATE)

        amount = models.ExchangeRate.get_rate("gbp", selected_currency) * charged_amount
        amount_int = int(round(amount * decimal.Decimal(100)))

        if selected_payment_method_type == "stripe_pm":
            ledger_item.descriptor = f"Card payment for {charge_state.ledger_item.descriptor}"
            ledger_item.type = models.LedgerItem.TYPE_CARD
            try:
                payment_intent = stripe.PaymentIntent.create(
                    amount=amount_int,
                    currency=selected_currency,
                    customer=charge_state.account.get_stripe_id(),
                    description=charge_state.ledger_item.descriptor,
                    receipt_email=charge_state.account.user.email,
                    statement_descriptor_suffix="Top-up",
                    payment_method=selected_payment_method_id,
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

            ledger_item.type_id = payment_intent['id']
            ledger_item.save(mail=False)
            charge_state.payment_ledger_item = ledger_item
            charge_state.save()
            update_from_payment_intent(payment_intent, ledger_item)
        elif selected_payment_method_type == "bacs_mandate_stripe":
            payment_intent = stripe.PaymentIntent.create(
                amount=amount_int,
                currency=selected_currency,
                customer=charge_state.account.get_stripe_id(),
                description=charge_state.ledger_item.descriptor,
                receipt_email=charge_state.account.user.email,
                statement_descriptor_suffix="Top-up",
                payment_method=selected_payment_method_id,
                payment_method_types=["bacs_debit"],
                confirm=True,
                off_session=True,
            )

            ledger_item.descriptor = f"BACS Direct Debit payment for {charge_state.ledger_item.descriptor}"
            ledger_item.type = models.LedgerItem.TYPE_CARD
            ledger_item.state = models.LedgerItem.STATE_PROCESSING
            ledger_item.type_id = payment_intent['id']
            ledger_item.save(mail=False)
            charge_state.payment_ledger_item = ledger_item
            charge_state.save()
            update_from_payment_intent(payment_intent, ledger_item)
        elif selected_payment_method_type == "sepa_mandate_stripe":
            payment_intent = stripe.PaymentIntent.create(
                amount=amount_int,
                currency=selected_currency,
                customer=charge_state.account.get_stripe_id(),
                description=charge_state.ledger_item.descriptor,
                receipt_email=charge_state.account.user.email,
                statement_descriptor_suffix="Top-up",
                payment_method=selected_payment_method_id,
                payment_method_types=["sepa_debit"],
                confirm=True,
                off_session=True,
            )

            ledger_item.descriptor = f"SEPA Direct Debit payment for {charge_state.ledger_item.descriptor}"
            ledger_item.type = models.LedgerItem.TYPE_SEPA
            ledger_item.state = models.LedgerItem.STATE_PROCESSING
            ledger_item.type_id = payment_intent['id']
            ledger_item.save()
            charge_state.payment_ledger_item = ledger_item
            charge_state.save(mail=False)
            update_from_payment_intent(payment_intent, ledger_item)
        elif selected_payment_method_type == "ach_mandate_gc":
            payment = apps.gocardless_client.payments.create(params={
                "amount": amount_int,
                "currency": selected_currency.upper(),
                "description": charge_state.ledger_item.descriptor,
                "retry_if_possible": False,
                "links": {
                    "mandate": selected_payment_method_id.mandate_id
                }
            })

            ledger_item.descriptor = f"ACH Direct Debit payment for {charge_state.ledger_item.descriptor}"
            ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
            ledger_item.type_id = payment.id
            ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
            ledger_item.evidence_ach_mandate = selected_payment_method_id
            ledger_item.save(mail=False)
        elif selected_payment_method_type == "autogiro_mandate_gc":
            payment = apps.gocardless_client.payments.create(params={
                "amount": amount_int,
                "currency": selected_currency.upper(),
                "description": charge_state.ledger_item.descriptor,
                "retry_if_possible": False,
                "links": {
                    "mandate": selected_payment_method_id.mandate_id
                }
            })

            ledger_item.descriptor = f"Autogiro payment for {charge_state.ledger_item.descriptor}"
            ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
            ledger_item.type_id = payment.id
            ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
            ledger_item.evidence_autogiro_mandate = selected_payment_method_id
            ledger_item.save(mail=False)
        elif selected_payment_method_type == "bacs_mandate_gc":
            payment = apps.gocardless_client.payments.create(params={
                "amount": amount_int,
                "currency": selected_currency.upper(),
                "description": charge_state.ledger_item.descriptor,
                "retry_if_possible": False,
                "links": {
                    "mandate": selected_payment_method_id.mandate_id
                }
            })

            ledger_item.descriptor = f"BACS Direct Debit payment for {charge_state.ledger_item.descriptor}"
            ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
            ledger_item.type_id = payment.id
            ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
            ledger_item.evidence_gc_bacs_mandate = selected_payment_method_id
            ledger_item.save(mail=False)
        elif selected_payment_method_type == "becs_mandate_gc":
            payment = apps.gocardless_client.payments.create(params={
                "amount": amount_int,
                "currency": selected_currency.upper(),
                "description": charge_state.ledger_item.descriptor,
                "retry_if_possible": False,
                "links": {
                    "mandate": selected_payment_method_id.mandate_id
                }
            })

            ledger_item.descriptor = f"BECS Direct Debit payment for {charge_state.ledger_item.descriptor}"
            ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
            ledger_item.type_id = payment.id
            ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
            ledger_item.evidence_becs_mandate = selected_payment_method_id
            ledger_item.save(mail=False)
        elif selected_payment_method_type == "becs_nz_mandate_gc":
            payment = apps.gocardless_client.payments.create(params={
                "amount": amount_int,
                "currency": selected_currency.upper(),
                "description": charge_state.ledger_item.descriptor,
                "retry_if_possible": False,
                "links": {
                    "mandate": selected_payment_method_id.mandate_id
                }
            })

            ledger_item.descriptor = f"BECS NZ Direct Debit payment for {charge_state.ledger_item.descriptor}"
            ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
            ledger_item.type_id = payment.id
            ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
            ledger_item.evidence_becs_nz_mandate = selected_payment_method_id
            ledger_item.save(mail=False)
        elif selected_payment_method_type == "betalingsservice_mandate_gc":
            payment = apps.gocardless_client.payments.create(params={
                "amount": amount_int,
                "currency": selected_currency.upper(),
                "description": charge_state.ledger_item.descriptor,
                "retry_if_possible": False,
                "links": {
                    "mandate": selected_payment_method_id.mandate_id
                }
            })

            ledger_item.descriptor = f"Betalingsservice payment for {charge_state.ledger_item.descriptor}"
            ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
            ledger_item.type_id = payment.id
            ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
            ledger_item.evidence_betalingsservice_mandate = selected_payment_method_id
            ledger_item.save(mail=False)
        elif selected_payment_method_type == "pad_mandate_gc":
            payment = apps.gocardless_client.payments.create(params={
                "amount": amount_int,
                "currency": selected_currency.upper(),
                "description": charge_state.ledger_item.descriptor,
                "retry_if_possible": False,
                "links": {
                    "mandate": selected_payment_method_id.mandate_id
                }
            })

            ledger_item.descriptor = f"PAD Direct Debit payment for {charge_state.ledger_item.descriptor}"
            ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
            ledger_item.type_id = payment.id
            ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
            ledger_item.evidence_pad_mandate = selected_payment_method_id
            ledger_item.save(mail=False)
        elif selected_payment_method_type == "sepa_mandate_gc":
            payment = apps.gocardless_client.payments.create(params={
                "amount": amount_int,
                "currency": selected_currency.upper(),
                "description": charge_state.ledger_item.descriptor,
                "retry_if_possible": False,
                "links": {
                    "mandate": selected_payment_method_id.mandate_id
                }
            })

            ledger_item.descriptor = f"SEPA Direct Debit payment for {charge_state.ledger_item.descriptor}"
            ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
            ledger_item.type_id = payment.id
            ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
            ledger_item.evidence_gc_sepa_mandate = selected_payment_method_id
            ledger_item.save(mail=False)
    else:
        charge_state.ledger_item.state = models.LedgerItem.STATE_COMPLETED
        charge_state.ledger_item.save(mail=False)


def charge_account(account: models.Account, amount: decimal.Decimal, descriptor: str, type_id: str, can_reject=True,
                   off_session=True, return_uri=None, notif_queue=None, supports_delayed=False, mail=True,
                   force_mail=False):
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
        can_reject=can_reject
    )

    if not account:
        if off_session:
            raise ChargeError(None, "Account does not exist")

        ledger_item.save(mail=False)
        charge_state.save()
        try_update_charge_state(ledger_item, mail=mail, force_mail=force_mail)
        raise ChargeStateRequiresActionError(
            charge_state, settings.EXTERNAL_URL_BASE + reverse('complete_order', args=(charge_state.id,))
        )

    if off_session:
        ledger_item.save(mail=False)
        charge_state.save()
        try:
            attempt_charge_off_session(charge_state)
            try_update_charge_state(ledger_item, mail=mail, force_mail=force_mail)
            return charge_state
        except ChargeError as e:
            if not e.must_reject and not can_reject:
                ledger_item.state = models.LedgerItem.STATE_COMPLETED

            charge_state.last_error = e.message
            charge_state.save()
            e.charge_state = charge_state
            if supports_delayed:
                ledger_item.save(mail=mail, force_mail=force_mail)
                raise ChargeStateRequiresActionError(
                    charge_state, settings.EXTERNAL_URL_BASE + reverse('complete_order', args=(charge_state.id,))
                )
            else:
                ledger_item.state = ledger_item.STATE_FAILED
            ledger_item.save(mail=mail, force_mail=force_mail)
            raise e
    else:
        from_account_balance = min(account.balance, -ledger_item.amount)
        left_to_be_paid = -(ledger_item.amount + from_account_balance)
        needs_payment = left_to_be_paid > 0

        if needs_payment:
            ledger_item.save(mail=False)
            charge_state.save()
            try_update_charge_state(ledger_item, mail=mail, force_mail=force_mail)
            raise ChargeStateRequiresActionError(
                charge_state, settings.EXTERNAL_URL_BASE + reverse('complete_order', args=(charge_state.id,))
            )
        else:
            ledger_item.state = models.LedgerItem.STATE_COMPLETED
            ledger_item.save(mail=mail, force_mail=force_mail)
            return charge_state


def process_ledger_item_refund(ledger_item: models.LedgerItem, amount: decimal.Decimal):
    if ledger_item.type == ledger_item.TYPE_GIROPAY:
        payment_intent = stripe.PaymentIntent.retrieve(ledger_item.type_id)
        payment_amount = decimal.Decimal(payment_intent["amount"]) / decimal.Decimal(100)
        exchange_rate = payment_amount / ledger_item.charged_amount
        int_refund = int(round(amount * exchange_rate * decimal.Decimal(100)))
        refund = stripe.Refund.create(
            payment_intent=payment_intent["id"],
            amount=int_refund,
            reason="requested_by_customer",
        )
        ledger_item = models.LedgerItem(
            account=ledger_item.account,
            type=ledger_item.TYPE_STRIPE_REFUND,
            type_id=refund['id'],
            amount=-amount / (1 + ledger_item.vat_rate),
            charged_amount=-amount,
            descriptor=f"Refund: {ledger_item.descriptor}",
            is_reversal=True,
            reversal_for=ledger_item,
            timestamp=timezone.now()
        )
        update_from_stripe_refund(refund, ledger_item)


def update_from_payment_intent(payment_intent, ledger_item: models.LedgerItem = None):
    ledger_item = models.LedgerItem.objects.filter(
        Q(type=models.LedgerItem.TYPE_CARD) | Q(type=models.LedgerItem.TYPE_SEPA) |
        Q(type=models.LedgerItem.TYPE_SOFORT) | Q(type=models.LedgerItem.TYPE_GIROPAY) |
        Q(type=models.LedgerItem.TYPE_BANCONTACT) | Q(type=models.LedgerItem.TYPE_EPS) |
        Q(type=models.LedgerItem.TYPE_IDEAL) | Q(type=models.LedgerItem.TYPE_P24) |
        Q(type=models.LedgerItem.TYPE_STRIPE_BACS)
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
            ledger_item.evidence_stripe_pm = known_payment_method
            ledger_item.save()
    elif payment_intent["status"] == "processing":
        ledger_item.state = models.LedgerItem.STATE_PROCESSING
        ledger_item.save()
    elif payment_intent["status"] == "requires_action":
        ledger_item.state = models.LedgerItem.STATE_PENDING
        ledger_item.save()

        if payment_intent["next_action"]["type"] == "display_bank_transfer_instructions" and \
                "display_bank_transfer_instructions" in payment_intent["next_action"]:
            bank_instructions = payment_intent["next_action"]["display_bank_transfer_instructions"]
            if bank_instructions["type"] == "sort_code":
                models.AccountStripeVirtualUKBank.objects.update_or_create(
                    sort_code=bank_instructions["sort_code"]["sort_code"],
                    account_number=bank_instructions["sort_code"]["account_number"],
                    defaults={
                        "account": ledger_item.account
                    }
                )
    elif (payment_intent["status"] == "requires_payment_method" and payment_intent["last_payment_error"]) \
            or payment_intent["status"] == "canceled":
        try:
            charge_state = ledger_item.charge_state_payment
        except django.core.exceptions.ObjectDoesNotExist:
            charge_state = None
        ledger_item.state = models.LedgerItem.STATE_FAILED
        ledger_item.save()
        if charge_state:
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

    for refund in charge["refunds"]["data"]:
        update_from_stripe_refund(refund)

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


def update_from_stripe_refund(refund, ledger_item=None):
    ledger_item = models.LedgerItem.objects.filter(
        type=models.LedgerItem.TYPE_STRIPE_REFUND,
        type_id=refund['id']
    ).first() if not ledger_item else ledger_item

    if not ledger_item and refund["pamyent_intent"]:
        payment_ledger_item = models.LedgerItem.objects.filter(
            Q(type=models.LedgerItem.TYPE_CARD) | Q(type=models.LedgerItem.TYPE_SEPA) |
            Q(type=models.LedgerItem.TYPE_SOFORT) | Q(type=models.LedgerItem.TYPE_GIROPAY) |
            Q(type=models.LedgerItem.TYPE_BANCONTACT) | Q(type=models.LedgerItem.TYPE_EPS) |
            Q(type=models.LedgerItem.TYPE_IDEAL) | Q(type=models.LedgerItem.TYPE_P24) |
            Q(type=models.LedgerItem.TYPE_STRIPE_BACS)
        ).filter(
            type_id=refund['payment_intent']
        ).first()
        if payment_ledger_item:
            amount_refunded_local = decimal.Decimal(refund["amount"]) / decimal.Decimal(100)
            payment_intent = stripe.PaymentIntent.retrieve(payment_ledger_item.type_id)
            payment_amount = decimal.Decimal(payment_intent["amount"]) / decimal.Decimal(100)
            exchange_rate = payment_amount / payment_ledger_item.charged_amount
            amount_refunded = (amount_refunded_local / exchange_rate).quantize(decimal.Decimal("1.00"))

            ledger_item = models.LedgerItem(
                account=payment_ledger_item.account,
                type=models.LedgerItem.TYPE_STRIPE_REFUND,
                type_id=refund['id'],
                amount=-amount_refunded / (1 + payment_ledger_item.vat_rate),
                charged_amount=-amount_refunded,
                descriptor=f"Refund: {payment_ledger_item.descriptor}",
                is_reversal=True,
                reversal_for=payment_ledger_item,
                timestamp=datetime.datetime.utcfromtimestamp(refund['created'])
            )

    if not ledger_item:
        return

    if refund["status"] == "pending":
        ledger_item.state = models.LedgerItem.STATE_PROCESSING
    elif refund["status"] == "succeeded":
        ledger_item.state = models.LedgerItem.STATE_COMPLETED
    elif refund["status"] in ("failed", "canceled"):
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


def update_from_gc_payment(payment_id, ledger_item=None):
    ledger_item = models.LedgerItem.objects.filter(
        type=models.LedgerItem.TYPE_GOCARDLESS, type_id=payment_id
    ).first() if not ledger_item else ledger_item

    if not ledger_item:
        return

    payment = apps.gocardless_client.payments.get(payment_id)
    if payment.status == "pending_submission":
        ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
        ledger_item.save()
    elif payment.status == "submitted":
        ledger_item.state = models.LedgerItem.STATE_PROCESSING
        ledger_item.save()
    elif payment.status in ("confirmed", "paid_out"):
        ledger_item.state = models.LedgerItem.STATE_COMPLETED
        ledger_item.save()
    elif payment.status in ("failed", "cancelled", "customer_approval_denied", "charged_back"):
        ledger_item.state = models.LedgerItem.STATE_FAILED
        ledger_item.save()


def setup_intent_succeeded(setup_intent):
    if "sepa_debit" in setup_intent["payment_method_types"]:
        models.SEPAMandate.sync_mandate(
            setup_intent["mandate"],
            models.Account.objects.filter(stripe_customer_id=setup_intent["customer"]).first()
        )


def balance_funded(balance_transaction):
    account = models.Account.objects.filter(
        stripe_customer_id=balance_transaction["customer"]
    ).first()

    if not account:
        return

    can_sell, can_sell_reason = account.can_sell
    if not can_sell:
        return

    balance_transaction = stripe.Customer.retrieve_balance_transaction(
        balance_transaction["customer"],
        balance_transaction["id"]
    )
    if balance_transaction["balance_type"] != "cash" or balance_transaction["type"] != "adjustment" \
            or balance_transaction["transaction_type"] != "deposit" \
            or balance_transaction["deposit"]["type"] != "funding":
        return

    customer = stripe.Customer.retrieve(balance_transaction["customer"], expand=["balances"])
    available_balance = next(filter(
        lambda b: b["currency"] == balance_transaction["currency"],
        customer["balances"]["cash"]["available"]
    ))["amount"]

    deposited_amount = min(-balance_transaction["amount"], available_balance)
    deposited_amount_decimal = decimal.Decimal(deposited_amount) / decimal.Decimal(100)
    deposited_amount_gbp = deposited_amount_decimal * models.ExchangeRate.get_rate(
        balance_transaction["currency"], "GBP")

    if balance_transaction["deposit"]["funding"]["type"] == "bank_transfer":
        ref = balance_transaction["deposit"]["funding"]["bank_transfer"]["reference"]
        transfer_type = balance_transaction["deposit"]["funding"]["bank_transfer"]["type"]
        if (account.billing_address.country_code.code.lower() == "gb" and transfer_type == "sort_code") \
                or not account.taxable:
            vat_rate = decimal.Decimal(0)
            if account.taxable:
                country_vat_rate = vat.get_vat_rate(
                    account.billing_address.country_code.code.upper(),
                    account.billing_address.postal_code,
                )
                if country_vat_rate is not None:
                    vat_rate = country_vat_rate

            payment_intent = stripe.PaymentIntent.create(
                amount=deposited_amount,
                currency=balance_transaction["currency"],
                customer=account.get_stripe_id(),
                description='Top-up',
                receipt_email=account.user.email,
                payment_method_types=["customer_balance"],
                payment_method_data={
                    "type": "customer_balance"
                },
                confirm=True,
            )

            new_ledger_item = models.LedgerItem(
                account=account,
                descriptor=f"Top-up by bank transfer: {ref}" if ref else "Top-up by bank transfer",
                amount=deposited_amount_gbp / (1 + vat_rate),
                charged_amount=deposited_amount_gbp,
                country_code=account.billing_address.country_code.code.lower(),
                vat_rate=vat_rate,
                type=models.LedgerItem.TYPE_STRIPE_BACS,
                type_id=payment_intent["id"],
                timestamp=datetime.datetime.utcfromtimestamp(balance_transaction["created"]),
                evidence_billing_address=account.billing_address,
                eur_exchange_rate=models.ExchangeRate.get_rate("gbp", "eur")
            )
            update_from_payment_intent(payment_intent, ledger_item=new_ledger_item)


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


# def find_account_evidence(account: models.Account, country_code):
#     evidence = {}
#     possible_bank_accounts = models.KnownBankAccount.objects.filter(
#         country_code=country_code,
#         account=account
#     )
#     if possible_bank_accounts.count():
#         evidence["evidence_bank_account"] = possible_bank_accounts.first()
#
#     if country_code == "us":
#         possible_ach_mandate = models.ACHMandate.objects.filter(
#             account=account,
#             active=True
#         )
#         if possible_ach_mandate.count():
#             evidence["evidence_ach_mandate"] = possible_ach_mandate.first()
#     elif country_code == "se":
#         possible_autogiro_mandate = models.AutogiroMandate.objects.filter(
#             account=account,
#             active=True
#         )
#         if possible_autogiro_mandate.count():
#             evidence["evidence_autogiro_mandate"] = possible_autogiro_mandate.first()
#     elif country_code == "gb":
#         possible_bacs_mandate = models.BACSMandate.objects.filter(
#             account=account,
#             active=True
#         )
#         if possible_bacs_mandate.count():
#             evidence["evidence_bacs_mandate"] = possible_bacs_mandate.first()
#         possible_gc_bacs_mandate = models.GCBACSMandate.objects.filter(
#             account=account,
#             active=True
#         )
#         if possible_gc_bacs_mandate.count():
#             evidence["evidence_gc_bacs_mandate"] = possible_gc_bacs_mandate.first()
#     elif country_code == "au":
#         possible_becs_mandate = models.BECSMandate.objects.filter(
#             account=account,
#             active=True
#         )
#         if possible_becs_mandate.count():
#             evidence["evidence_becs_mandate"] = possible_becs_mandate.first()
#     elif country_code == "nz":
#         possible_becs_nz_mandate = models.BECSNZMandate.objects.filter(
#             account=account,
#             active=True
#         )
#         if possible_becs_nz_mandate.count():
#             evidence["evidence_becs_nz_mandate"] = possible_becs_nz_mandate.first()
#     elif country_code == "dk":
#         possible_betalingsservice_mandate = models.BetalingsserviceMandate.objects.filter(
#             account=account,
#             active=True
#         )
#         if possible_betalingsservice_mandate.count():
#             evidence["evidence_betalingsservice_mandate"] = possible_betalingsservice_mandate.first()
#     elif country_code == "ca":
#         possible_pad_mandate = models.PADMandate.objects.filter(
#             account=account,
#             active=True
#         )
#         if possible_pad_mandate.count():
#             evidence["evidence_pad_mandate"] = possible_pad_mandate.first()
#
#     possible_sepa_mandates = models.SEPAMandate.objects.filter(
#         account=account,
#         active=True
#     )
#     for possible_sepa_mandate in possible_sepa_mandates:
#         payment_method = stripe.PaymentMethod.retrieve(possible_sepa_mandate.payment_method)
#         payment_method_country = utils.country_from_stripe_payment_method(payment_method)
#         if payment_method_country == country_code:
#             evidence["evidence_sepa_mandate"] = possible_sepa_mandate
#             break
#
#     possible_gc_sepa_mandates = models.GCSEPAMandate.objects.filter(
#         account=account,
#         active=True
#     )
#     for possible_gc_sepa_mandate in possible_gc_sepa_mandates:
#         mandate = apps.gocardless_client.mandates.get(possible_gc_sepa_mandate.mandate_id)
#         customer_bank_account = apps.gocardless_client.customer_bank_accounts.get(mandate.links.customer_bank_account)
#         payment_method_country = customer_bank_account.country_code.lower()
#         if payment_method_country == country_code:
#             evidence["evidence_gc_sepa_mandate"] = possible_gc_sepa_mandate
#             break
#
#     possible_stripe_pms = models.KnownStripePaymentMethod.objects.filter(
#         country_code=country_code,
#         account=account
#     )
#     if not possible_stripe_pms.count() and not bool(evidence):
#         sync_payment_methods(account)
#         possible_stripe_pms = models.KnownStripePaymentMethod.objects.filter(
#             country_code=country_code,
#             account=account
#         )
#     if possible_stripe_pms.count():
#         evidence["evidence_stripe_pm"] = possible_stripe_pms.first()
#
#     return evidence if bool(evidence) else None
