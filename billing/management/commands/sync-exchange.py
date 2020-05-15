from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
import requests
import datetime
import pytz
import json
import decimal
from billing import models


class Command(BaseCommand):
    help = 'Syncs exchange rates'

    def handle(self, *args, **options):
        r = requests.get(f"https://openexchangerates.org/api/latest.json?app_id={settings.OPEN_EXCHANGE_API_KEY}")
        if r.status_code != 200:
            raise CommandError(f"Error getting exchange rates: {r.text}")
        try:
            d = json.loads(r.text)
        except json.JSONDecodeError as e:
            raise CommandError(f"Error decoding json: {str(e)}")

        timestamp = datetime.datetime.fromtimestamp(d["timestamp"], pytz.utc)
        for currency, value in d["rates"].items():
            existing_model = models.ExchangeRate.objects.filter(currency=currency).first()
            if existing_model:
                existing_model.timestamp = timestamp
                existing_model.rate = decimal.Decimal(value)
                existing_model.save()
            else:
                new_model = models.ExchangeRate(
                    currency=currency,
                    timestamp=timestamp,
                    rate=decimal.Decimal(value)
                )
                new_model.save()
