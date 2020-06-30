from django.core.management.base import BaseCommand, CommandError
from django.template.loader import render_to_string
from dateutil import relativedelta
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
import datetime
import decimal
from billing import models, tasks

RETRY_INTERVAL = datetime.timedelta(days=1)
RETRY_TIME = datetime.timedelta(days=1)


def mail_success(subscription: models.Subscription, value: decimal.Decimal):
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
        bcc=['q@as207960.net']
    )
    email.attach_alternative(html_content, "text/html")
    email.send()


def mail_past_due(subscription: models.Subscription, value: decimal.Decimal, reason: str):
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
        bcc=['q@as207960.net']
    )
    email.attach_alternative(html_content, "text/html")
    email.send()


def mail_cancelled(subscription: models.Subscription, value: decimal.Decimal, reason: str):
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
        bcc=['q@as207960.net']
    )
    email.attach_alternative(html_content, "text/html")
    email.send()


class Command(BaseCommand):
    help = 'Runs the billing system background tasks once'

    def handle(self, *args, **options):
        now = timezone.now()
        plans = models.RecurringPlan.objects.all()
        for plan in plans:
            billing_interval = plan.billing_interval
            for subscription in plan.subscription_set.all():
                if subscription.state == subscription.STATE_CANCELLED:
                    print(f"Not charging subscription {subscription.id}: subscription cancelled")
                    continue

                next_bill = subscription.last_billed + billing_interval
                next_bill_attempt = subscription.last_bill_attempted + RETRY_INTERVAL
                if next_bill > now:
                    print(f"Not charging subscription {subscription.id}: not due for charge yet")
                    continue
                if next_bill_attempt > now:
                    print(f"Not charging subscription {subscription.id}: not due for charge retry yet")
                    continue

                charge = plan.calculate_charge(subscription.usage_in_period)

                subscription.last_bill_attempted = now
                subscription.save()

                try:
                    tasks.charge_account(subscription.account, charge, plan.name, f"sb_{subscription.id}")
                except tasks.ChargeError as e:
                    print(f"Failed to bill subscription {subscription.id}: {e.message}")
                    subscription.state = subscription.STATE_PAST_DUE
                    subscription.amount_unpaid = charge

                    subscription_fail = next_bill + RETRY_TIME
                    if subscription_fail <= now:
                        print(f"Retry time exceeded on subscription {subscription.id}: cancelling subscription")
                        subscription.state = subscription.STATE_CANCELLED
                        subscription.save()
                        mail_cancelled(subscription, charge, e.message)
                    else:
                        subscription.save()
                        mail_past_due(subscription, charge, e.message)

                    continue

                print(f"Successfully charged subscription {subscription.id}")
                subscription.state = subscription.STATE_ACTIVE
                subscription.last_billed = now
                subscription.amount_unpaid = decimal.Decimal("0")
                subscription.save()
                mail_success(subscription, charge)
