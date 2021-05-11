from django.core.management.base import BaseCommand
from django.db.models import Q
import stripe
from billing import models, tasks


class Command(BaseCommand):
    help = 'Updates all ledger items in non complete states'

    def handle(self, *args, **options):
        for ledger_item in models.LedgerItem.objects.filter(
                Q(state=models.LedgerItem.STATE_PENDING) | Q(state=models.LedgerItem.STATE_PROCESSING)
        ):
            if ledger_item.type in (
                    models.LedgerItem.TYPE_CARD, models.LedgerItem.TYPE_SEPA, models.LedgerItem.TYPE_SOFORT,
                    models.LedgerItem.TYPE_GIROPAY, models.LedgerItem.TYPE_BANCONTACT, models.LedgerItem.TYPE_EPS,
                    models.LedgerItem.TYPE_IDEAL, models.LedgerItem.TYPE_STRIPE_BACS,
            ):
                payment_intent = stripe.PaymentIntent.retrieve(ledger_item.type_id)
                tasks.update_from_payment_intent(payment_intent, ledger_item)
            elif ledger_item.type == models.LedgerItem.TYPE_SOURCES:
                source = stripe.Source.retrieve(ledger_item.type_id)
                tasks.update_from_source(source, ledger_item)
            elif ledger_item.type == models.LedgerItem.TYPE_CHARGES:
                charge = stripe.Charge.retrieve(ledger_item.type_id)
                tasks.update_from_charge(charge, ledger_item)
            elif ledger_item.type == models.LedgerItem.TYPE_CHECKOUT:
                session = stripe.checkout.Session.retrieve(ledger_item.type_id)
                tasks.update_from_checkout_session(session, ledger_item)
            elif ledger_item.type == models.LedgerItem.TYPE_STRIPE_REFUND:
                refund = stripe.Refund.retrieve(ledger_item.type_id)
                tasks.update_from_stripe_refund(refund, ledger_item)
            elif ledger_item.type == models.LedgerItem.TYPE_GOCARDLESS:
                tasks.update_from_gc_payment(ledger_item.type_id, ledger_item)
