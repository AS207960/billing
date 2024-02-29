from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from billing import models, tasks
from billing.views import emails


class Command(BaseCommand):
    help = 'Attempt to bring all account balances back above negative'

    def handle(self, *args, **options):
        for account in models.Account.objects.all():
            if account.processing_and_completed_balance < -1:
                charge = 0 - account.processing_and_completed_balance
                try:
                    tasks.charge_account(account, charge, "Balance reconciliation", "")
                except tasks.ChargeError as e:
                    print(f"Failed to bill account {account.user.username}: {e.message}")
                    emails.send_email({
                        "subject": "Billing reconciliation failed",
                        "content": render_to_string("billing_email/billing_reconcile_fail.html", {
                            "reason": e.message
                        })
                    }, user=account.user)
            else:
                print(f"Not charging account {account.user.username}: balance not negative enough")
