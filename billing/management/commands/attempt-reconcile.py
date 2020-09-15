from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.core.mail import EmailMultiAlternatives
from billing import models, tasks


def mail_failed(account: models.Account, reason: str):
    context = {
        "name": account.user.first_name,
        "reason": reason
    }
    html_content = render_to_string("billing_email/billing_reconcile_fail.html", context)
    txt_content = render_to_string("billing_email/billing_reconcile_fail.txt", context)

    email = EmailMultiAlternatives(
        subject='Billing reconciliation failed',
        body=txt_content,
        to=[account.user.email],
        bcc=['q@as207960.net'],
        reply_to=['info@as207960.net']
    )
    email.attach_alternative(html_content, "text/html")
    email.send()


class Command(BaseCommand):
    help = 'Attempt to bring all account balances back above negative'

    def handle(self, *args, **options):
        for account in models.Account.objects.all():
            if account.processing_and_completed_balance < 1:
                charge = 0 - account.processing_and_completed_balance
                try:
                    tasks.charge_account(account, charge, "Balance reconciliation", "")
                except tasks.ChargeError as e:
                    print(f"Failed to bill account {account.user.username}: {e.message}")
                    mail_failed(account, e.message)
            else:
                print(f"Not charging account {account.user.username}: balance not negative enough")
