from . import models, views
from django.shortcuts import reverse
from django.utils import timezone
from django.conf import settings
from django.dispatch import receiver
from django.db.models.signals import pre_save
import multiprocessing
import sentry_sdk
import pywebpush
import json
import decimal
import stripe.error


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
    elif ledger_item.state == ledger_item.STATE_PROCESSING:
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
def send_item_notif(sender, instance, **kwargs):
    old_instance = models.LedgerItem.objects.filter(id=instance.id).first()
    if not old_instance:
        p = multiprocessing.Process(target=alert_account, args=(instance.account, instance), kwargs={"new": True})
    elif old_instance.state != instance.state:
        p = multiprocessing.Process(target=alert_account, args=(instance.account, instance))
    else:
        return
    p.start()


class ChargeError(Exception):
    def __init__(self, payment_ledger_item, message):
        self.payment_ledger_item = payment_ledger_item
        self.message = message


class RequiresActionError(Exception):
    def __init__(self, payment_ledger_item, redirect_url=None):
        self.payment_ledger_item = payment_ledger_item
        self.redirect_url = redirect_url


class ChargeStateRequiresActionError(Exception):
    def __init__(self, charge_state, redirect_url=None):
        self.charge_state = charge_state
        self.redirect_url = redirect_url


def attempt_charge_account(account: models.Account, amount_gbp: decimal.Decimal, off_session=True, return_uri=None):
    if not account.default_stripe_payment_method_id:
        cards = list(stripe.PaymentMethod.list(
            customer=account.get_stripe_id(),
            type="card"
        ).auto_paging_iter())
        if len(cards):
            account.default_stripe_payment_method_id = cards[0]["id"]

    if account.default_stripe_payment_method_id:
        payment_method = stripe.PaymentMethod.retrieve(account.default_stripe_payment_method_id)
        currency = "gbp"
        if payment_method["type"] == "sepa_debit":
            currency = "eur"

        if amount_gbp < decimal.Decimal(1):
            amount_gbp = decimal.Decimal(1)

        amount = models.ExchangeRate.get_rate("gbp", currency) * amount_gbp
        amount_int = int(amount * decimal.Decimal(100))

        ledger_item = models.LedgerItem(
            account=account,
            descriptor="Automatic top-up",
            amount=amount_gbp,
            type=models.LedgerItem.TYPE_CARD,
        )

        try:
            payment_intent = stripe.PaymentIntent.create(
                amount=amount_int,
                currency=currency,
                customer=account.get_stripe_id(),
                description='Top-up',
                receipt_email=account.user.email,
                statement_descriptor_suffix="Top-up",
                payment_method=account.default_stripe_payment_method_id,
                payment_method_types=[payment_method["type"]],
                confirm=True,
                return_url=settings.EXTERNAL_URL_BASE + reverse('dashboard') if not return_uri else return_uri,
                off_session=off_session,
            )
        except (stripe.error.CardError, stripe.error.InvalidRequestError) as e:
            err = e.error
            ledger_item.type_id = err.payment_intent['id']
            ledger_item.state = ledger_item.STATE_FAILED
            ledger_item.save()
            raise ChargeError(err.payment_intent, err.message)

        ledger_item.state = ledger_item.STATE_PROCESSING
        ledger_item.type_id = payment_intent['id']

        if payment_intent["status"] == "requires_action":
            if payment_intent["next_action"]["type"] == "use_stripe_sdk":
                ledger_item.type_id = payment_intent['id']
                ledger_item.state = ledger_item.STATE_FAILED
                ledger_item.save()
                raise ChargeError(ledger_item, "Card requires authentication")
            elif payment_intent["next_action"]["type"] == "redirect_to_url":
                ledger_item.save()
                raise RequiresActionError(
                    payment_ledger_item=ledger_item,
                    redirect_url=payment_intent["next_action"]["redirect_to_url"]["url"]
                )
        ledger_item.save()
        return ledger_item
    else:
        raise ChargeError(None, "No default payment method available to charge")


def confirm_payment(charge_state_id):
    charge_state = models.ChargeState.objects.get(pk=charge_state_id)
    payment_intent = stripe.PaymentIntent.retrieve(charge_state.payment_ledger_item.type_id) \
        if (charge_state.payment_ledger_item and charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_CARD)\
        else None

    if payment_intent:
        if payment_intent.get("last_payment_error"):
            charge_state.payment_ledger_item.state = models.LedgerItem.STATE_FAILED
            charge_state.payment_ledger_item.save()
            if charge_state.ledger_item:
                charge_state.ledger_item.state = charge_state.ledger_item.STATE_FAILED
                charge_state.ledger_item.save()
            raise ChargeError(charge_state.payment_ledger_item, payment_intent["last_payment_error"]["message"])
        else:
            try:
                payment_intent.confirm()
            except (stripe.error.CardError, stripe.error.InvalidRequestError) as e:
                charge_state.payment_ledger_item.state = models.LedgerItem.STATE_FAILED
                charge_state.payment_ledger_item.save()
                if charge_state.ledger_item:
                    charge_state.ledger_item.state = models.LedgerItem.STATE_FAILED
                    charge_state.ledger_item.save()
                raise ChargeError(charge_state.payment_ledger_item, e.error.message)

    if charge_state.payment_ledger_item:
        if charge_state.payment_ledger_item.type in (
            models.LedgerItem.TYPE_CARD, models.LedgerItem.TYPE_SEPA
        ):
            payment_intent = stripe.PaymentIntent.retrieve(charge_state.payment_ledger_item.type_id)
            views.update_from_payment_intent(payment_intent, charge_state.payment_ledger_item)
        elif charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_SOURCES:
            source = stripe.Source.retrieve(charge_state.payment_ledger_item.type_id)
            views.update_from_source(source, charge_state.payment_ledger_item)
        elif charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_CHARGES:
            charge = stripe.Charge.retrieve(charge_state.payment_ledger_item.type_id)
            views.update_from_charge(charge, charge_state.payment_ledger_item)
        elif charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_CHECKOUT:
            session = stripe.checkout.Session.retrieve(charge_state.payment_ledger_item.type_id)
            views.update_from_charge(session, charge_state.payment_ledger_item)

        if charge_state.payment_ledger_item.state in (
                models.LedgerItem.STATE_COMPLETED, models.LedgerItem.STATE_PROCESSING
        ):
            if charge_state.ledger_item:
                charge_state.ledger_item.state = charge_state.ledger_item.STATE_COMPLETED
                charge_state.ledger_item.save()
        elif charge_state.ledger_item:
            charge_state.ledger_item.state = charge_state.ledger_item.STATE_FAILED
            charge_state.ledger_item.save()
            raise ChargeError(None, "Payment failed")
    elif charge_state.ledger_item:
        if charge_state.account.balance >= charge_state.ledger_item.amount:
            charge_state.ledger_item.state = charge_state.ledger_item.STATE_COMPLETED
            charge_state.ledger_item.save()
        else:
            raise ChargeError(None, "Insufficient funds in your account")


def charge_account(account: models.Account, amount: decimal.Decimal, descriptor: str, type_id: str, can_reject=True,
                   off_session=True, return_uri=None):
    ledger_item = models.LedgerItem(
        account=account,
        descriptor=descriptor,
        amount=-amount,
        type=models.LedgerItem.TYPE_CHARGE,
        type_id=type_id
    )
    charge_state = models.ChargeState(
        account=account,
        ledger_item=ledger_item,
        return_uri=return_uri
    )

    payment_ledger_item = None
    if account.balance - amount < 0:
        charge_amount = -(account.balance - amount)
        try:
            payment_ledger_item = attempt_charge_account(
                account, charge_amount, off_session=off_session, return_uri=return_uri
            )
        except ChargeError as e:
            payment_ledger_item = e.payment_ledger_item
            if can_reject:
                ledger_item.timestamp = timezone.now()
                ledger_item.state = ledger_item.STATE_FAILED
                ledger_item.save()
                charge_state.payment_ledger_item = payment_ledger_item
                charge_state.save()
                raise e
        except RequiresActionError as e:
            payment_ledger_item = e.payment_ledger_item
            if can_reject:
                ledger_item.timestamp = timezone.now()
                ledger_item.state = ledger_item.STATE_PROCESSING
                ledger_item.save()
                charge_state.payment_ledger_item = payment_ledger_item
                charge_state.save()
                raise ChargeStateRequiresActionError(charge_state, e.redirect_url)

    ledger_item.timestamp = timezone.now()
    ledger_item.state = ledger_item.STATE_COMPLETED
    ledger_item.save()
    charge_state.payment_ledger_item = payment_ledger_item
    charge_state.save()
