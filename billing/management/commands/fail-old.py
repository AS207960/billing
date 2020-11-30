from django.core.management.base import BaseCommand
import datetime
from django.utils import timezone
from billing import models

FAIL_TIME = datetime.timedelta(days=1)


class Command(BaseCommand):
    help = 'Fails all old pending charges'

    def handle(self, *args, **options):
        now = timezone.now()
        fail_threshold = now + FAIL_TIME

        for ledger_item in models.LedgerItem.objects.filter(
            type=models.LedgerItem.TYPE_CHARGE,
            state=models.LedgerItem.STATE_PENDING,
            last_state_change_timestamp__lt=fail_threshold
        ):
            print(ledger_item)
            ledger_item.state = models.LedgerItem.STATE_FAILED
            ledger_item.save()
