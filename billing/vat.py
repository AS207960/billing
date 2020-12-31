import decimal
from django.utils import timezone
import datetime
import pytz

DO_NOT_SELL = [
    "al",  # Albania
    "bh",  # Bahrain
    "bd",  # Bangladesh
    "bb",  # Barbados
    "by",  # Belarus
    "cl",  # Chile
    "co",  # Colombia
    "in",  # India
    "kz",  # Kazakhstan
    "xk",  # Kosovo (provisional ISO code)
    "kw",  # Kuwait
    "mx",  # Mexico
    "md",  # Moldova
    "ma",  # Morocco
    "om",  # Oman
    "ru",  # Russia
    "sa",  # Saudi Arabia
    "rs",  # Serbia
    "kr",  # South Korea
    "tr",  # Turkey
    "ug",  # Uganda
    "ua",  # Ukraine
    "ae",  # UAE
    "uy",  # Uruguay
    "uz",  # Uzbekistan
]

VAT_RATES_PRE_2021 = {}
VAT_RATES_PRE_2021_DATE = datetime.datetime(2020, 12, 31, 23, 00, 00, tzinfo=pytz.utc)

VAT_RATES_FROM_2021 = {
    "at": decimal.Decimal("0.20"),
    "be": decimal.Decimal("0.21"),
    "bg": decimal.Decimal("0.20"),
    "cy": decimal.Decimal("0.19"),
    "cz": decimal.Decimal("0.21"),
    "de": decimal.Decimal("0.19"),
    "dk": decimal.Decimal("0.25"),
    "ee": decimal.Decimal("0.20"),
    "gr": decimal.Decimal("0.24"),
    "es": decimal.Decimal("0.21"),
    "fi": decimal.Decimal("0.24"),
    "fr": decimal.Decimal("0.20"),
    "hr": decimal.Decimal("0.25"),
    "hu": decimal.Decimal("0.27"),
    "ie": decimal.Decimal("0.23"),
    "it": decimal.Decimal("0.22"),
    "lt": decimal.Decimal("0.21"),
    "lu": decimal.Decimal("0.17"),
    "lv": decimal.Decimal("0.21"),
    "mt": decimal.Decimal("0.18"),
    "nl": decimal.Decimal("0.21"),
    "pl": decimal.Decimal("0.23"),
    "pt": decimal.Decimal("0.23"),
    "ro": decimal.Decimal("0.19"),
    "se": decimal.Decimal("0.25"),
    "sl": decimal.Decimal("0.22"),
    "sk": decimal.Decimal("0.20"),
}


def get_vat_rate(country):
    if timezone.now() < VAT_RATES_PRE_2021_DATE:
        vat_rates = VAT_RATES_PRE_2021
    else:
        vat_rates = VAT_RATES_FROM_2021

    return vat_rates.get(country.lower())


def need_billing_evidence():
    return False


def get_vies_country_code(iso_code: str):
    iso_code = iso_code.upper()
    countries = ["AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "GR", "ES", "FI", "FR", "GB", "HU", "IE", "IT", "LT",
                 "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SK"]
    if iso_code not in countries:
        return None
    if iso_code == "GR":
        return "EL"
    else:
        return iso_code
