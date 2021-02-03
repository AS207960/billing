from django.core.management.base import BaseCommand
from django.utils import timezone
from billing import models, tasks


class Command(BaseCommand):
    help = 'Runs the billing system background tasks once'

    def handle(self, *args, **options):
        now = timezone.now()
        plans = models.RecurringPlan.objects.all()
        for plan in plans:
            billing_interval = plan.billing_interval
            for subscription in plan.subscription_set.all():
                if subscription.state == subscription.STATE_PENDING:
                    print(f"Not charging subscription {subscription.id}: subscription pending")
                    continue
                if subscription.state == subscription.STATE_CANCELLED:
                    print(f"Not charging subscription {subscription.id}: subscription cancelled")
                    continue

                next_bill = subscription.last_billed + billing_interval
                if next_bill > now:
                    print(f"Not charging subscription {subscription.id}: not due for charge yet")
                    continue

                charge = plan.calculate_charge(subscription.usage_in_period)

                subscription_charge = models.SubscriptionCharge(
                    subscription=subscription,
                    timestamp=now,
                    last_bill_attempted=now,
                    amount=charge,
                )

                try:
                    charge_state = tasks.charge_account(
                        subscription.account, charge, plan.name, f"sb_{subscription.id}",
                        can_reject=True, off_session=True, supports_delayed=True, mail=False
                    )
                except (tasks.ChargeError, tasks.ChargeStateRequiresActionError) as e:
                    ledger_item = e.charge_state.ledger_item
                else:
                    ledger_item = charge_state.ledger_item

                subscription_charge.last_ledger_item = ledger_item
                subscription_charge.save()
                ledger_item.subscription_charge = subscription_charge
                ledger_item.save(mail=True, force_mail=True)

                subscription.last_billed = now
                subscription.save()

                print(f"Charged subscription {subscription.id}")

        retry_time = now - tasks.SUBSCRIPTION_RETRY_INTERVAL
        retry_charges = models.SubscriptionCharge.objects.filter(
            subscription__state__in=(models.Subscription.STATE_PAST_DUE, models.Subscription.STATE_ACTIVE),
            last_ledger_item__state=models.LedgerItem.STATE_FAILED,
            last_bill_attempted__lte=retry_time
        )
        for subscription_charge in retry_charges:
            if subscription_charge.failed_bill_attempts < tasks.SUBSCRIPTION_RETRY_ATTEMPTS:
                try:
                    charge_state = tasks.charge_account(
                        subscription_charge.subscription.account, subscription_charge.amount,
                        subscription_charge.subscription.plan.name, f"sb_{subscription_charge.subscription.id}",
                        can_reject=True, off_session=True, supports_delayed=True
                    )
                except (tasks.ChargeError, tasks.ChargeStateRequiresActionError) as e:
                    e.charge_state.ledger_item.subscription_charge = subscription_charge
                    subscription_charge.last_ledger_item = e.charge_state.ledger_item
                    e.charge_state.ledger_item.save(mail=True, force_mail=True)
                else:
                    charge_state.ledger_item.subscription_charge = subscription_charge
                    subscription_charge.last_ledger_item = charge_state.ledger_item
                    charge_state.ledger_item.save(mail=True, force_mail=True)
                subscription_charge.last_bill_attempted = now
                subscription_charge.save()

                print(f"Retry charged subscription {subscription_charge.subscription.id}")
