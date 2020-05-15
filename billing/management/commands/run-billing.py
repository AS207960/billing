from django.core.management.base import BaseCommand, CommandError
from dateutil import relativedelta
from django.conf import settings
import requests
import datetime
import pytz
import json
import decimal
from billing import models


class Command(BaseCommand):
    help = 'Runs the billing system background tasks once'

    def handle(self, *args, **options):
        subscriptions = models.Subscription.objects.all()
        for subscription in subscriptions:
            if subscription.plan.billing_interval_unit == models.RecurringPlan.INTERVAL_DAY:
                billing_interval = datetime.timedelta(days=subscription.plan.billing_interval_value)
            elif subscription.plan.billing_interval_unit == models.RecurringPlan.INTERVAL_WEEK:
                billing_interval = datetime.timedelta(weeks=subscription.plan.billing_interval_value)
            elif subscription.plan.billing_interval_unit == models.RecurringPlan.INTERVAL_MONTH:
                billing_interval = relativedelta.relativedelta(months=1)
            print(subscription, billing_interval)
