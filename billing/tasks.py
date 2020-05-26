from . import models
from django.utils import timezone
from django.conf import settings
from django.dispatch import receiver
from django.db.models.signals import pre_save
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
        alert_account(instance.account, instance, new=True)
    elif old_instance.state != instance.state:
        alert_account(instance.account, instance)


class ChargeError(Exception):
    def __init__(self, message):
        self.message = message


def attempt_charge_account(account: models.Account, amount: decimal.Decimal):
    if account.default_stripe_payment_method_id:
        amount_int = int(amount * decimal.Decimal(100))

        if amount_int < 100:
            amount_int = 100
            amount = decimal.Decimal(1)

        ledger_item = models.LedgerItem(
            account=account,
            descriptor="Top-up by card",
            amount=amount,
            type=models.LedgerItem.TYPE_CARD,
        )

        try:
            payment_intent = stripe.PaymentIntent.create(
                amount=amount_int,
                currency='gbp',
                customer=account.get_stripe_id(),
                description='Top-up',
                receipt_email=account.user.email,
                statement_descriptor_suffix="Top-up",
                payment_method=account.default_stripe_payment_method_id,
                confirm=True,
                off_session=True,
            )
        except stripe.error.CardError as e:
            err = e.error
            ledger_item.type_id = err.payment_intent['id']
            ledger_item.state = ledger_item.STATE_FAILED
            ledger_item.save()
            raise ChargeError(err.message)

        ledger_item.state = ledger_item.STATE_COMPLETED
        ledger_item.type_id = payment_intent['id']
        ledger_item.save()
    else:
        raise ChargeError("No card available to charge")


def charge_account(account: models.Account, amount: decimal.Decimal, descriptor: str, type_id: str, can_reject=True):
    ledger_item = models.LedgerItem(
        account=account,
        descriptor=descriptor,
        amount=-amount,
        type=models.LedgerItem.TYPE_CHARGE,
        type_id=type_id
    )

    if account.balance - amount < 0:
        charge_amount = -(account.balance - amount)
        try:
            attempt_charge_account(account, charge_amount)
        except ChargeError as e:
            if can_reject:
                ledger_item.timestamp = timezone.now()
                ledger_item.state = ledger_item.STATE_FAILED
                ledger_item.save()
                raise e

    ledger_item.timestamp = timezone.now()
    ledger_item.state = ledger_item.STATE_COMPLETED
    ledger_item.save()
