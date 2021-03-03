from django.core.management.base import BaseCommand, CommandError
import xml.etree.ElementTree
import requests
import datetime
import pytz
import decimal
from billing import models


class Command(BaseCommand):
    help = 'Syncs exchange rates from ECB'

    def handle(self, *args, **options):
        r = requests.get(f"https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml")
        if r.status_code != 200:
            raise CommandError(f"Error getting exchange rates: {r.text}")
        try:
            d = xml.etree.ElementTree.fromstring(r.text)
        except xml.etree.ElementTree.ParseError as e:
            raise CommandError(f"Error decoding json: {str(e)}")

        namespaces = {
            "gesmes": "http://www.gesmes.org/xml/2002-08-01",
            "eurofxref": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref",
        }

        cube = d.find("eurofxref:Cube/eurofxref:Cube[@time]", namespaces=namespaces)
        if not cube:
            raise CommandError(f"Response did not contain Cube element")

        timestamp = datetime.datetime.strptime(cube.attrib.get("time"), "%Y-%m-%d")
        timestamp = timestamp.replace(tzinfo=pytz.utc, hour=0, minute=0, second=0)

        rates = cube.findall("./eurofxref:Cube[@rate][@currency]", namespaces=namespaces)

        currencies = [("EUR", decimal.Decimal("1.0"))]

        for rate in rates:
            currency = rate.attrib.get("currency")
            fx_rate = rate.attrib.get("rate")
            currencies.append((currency, decimal.Decimal(fx_rate)))

        for currency, value in currencies:
            models.ExchangeRate.objects.update_or_create(currency=currency, defaults={
                "timestamp": timestamp,
                "rate": value
            })
