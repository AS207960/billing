import datetime
import decimal
import uuid

import oauthlib.oauth2
import oauthlib.oauth2
import pytz
import typing
import requests_oauthlib
from django.conf import settings
from django.utils import timezone
import dataclasses
import enum

from . import utils

_hmrc_oauth_client = None
_hmrc_oauth_session = None


def get_hmrc_session():
    global _hmrc_oauth_client, _hmrc_oauth_session
    if settings.HMRC_CLIENT_ID:
        if not _hmrc_oauth_client:
            _hmrc_oauth_client = oauthlib.oauth2.BackendApplicationClient(client_id=settings.HMRC_CLIENT_ID)
        if not _hmrc_oauth_session:
            _hmrc_oauth_session = requests_oauthlib.OAuth2Session(client=_hmrc_oauth_client)
            _hmrc_oauth_session.fetch_token(
                token_url='https://test-api.service.hmrc.gov.uk/oauth/token' if settings.IS_TEST
                else 'https://api.service.hmrc.gov.uk/oauth/token',
                client_id=settings.HMRC_CLIENT_ID,
                client_secret=settings.HMRC_CLIENT_SECRET,
                include_client_id=True
            )

    return _hmrc_oauth_session


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
    "ug",  # Uganda
    "ua",  # Ukraine
    "ae",  # UAE
    "uy",  # Uruguay
    "uz",  # Uzbekistan
]

VAT_RATES_PRE_2021 = {}
VAT_RATES_PRE_2021_DATE = datetime.datetime(2020, 12, 31, 23, 00, 00, tzinfo=pytz.utc)

VAT_RATES_FROM_2021: typing.Dict[str, decimal.Decimal] = {
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

VAT_RATES_PRE_UK_REG_DATE = datetime.datetime(2021, 3, 28, 23, 00, 00, tzinfo=pytz.utc)
VAT_RATES_FROM_UK_REG: typing.Dict[str, decimal.Decimal] = {
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
    "gb": decimal.Decimal("0.20"),
    "im": decimal.Decimal("0.20"),
}

VAT_RATES_PRE_TR_REG_DATE = datetime.datetime(2021, 6, 28, 00, 00, 00, tzinfo=pytz.utc)
VAT_RATES_FROM_TR_REG: typing.Dict[str, decimal.Decimal] = {
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
    "gb": decimal.Decimal("0.20"),
    "tr": decimal.Decimal("0.18"),
}

VAT_RATES_PRE_KR_REG_DATE = datetime.datetime(2023, 4, 1, 00, 00, 00, tzinfo=pytz.utc)
VAT_RATES_FROM_KR_REG: typing.Dict[str, decimal.Decimal] = {
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
    "gb": decimal.Decimal("0.20"),
    "tr": decimal.Decimal("0.18"),
    "kr": decimal.Decimal("0.10"),
}

VAT_MOSS_COUNTRIES = (
    "at", "be", "bg", "cy", "cz", "de", "dk", "ee", "gr", "es", "fi", "fr", "xi", "hu", "ie", "it", "lt", "lu", "lv",
    "mt", "nl", "pl", "pt", "ro", "se", "sk"
)

COUNTRY_CURRENCIES = {
    "at": "EUR",
    "be": "EUR",
    "bg": "BGN",
    "cy": "EUR",
    "cz": "CZK",
    "de": "EUR",
    "dk": "DKK",
    "ee": "EUR",
    "gr": "EUR",
    "es": "EUR",
    "fi": "EUR",
    "fr": "EUR",
    "hr": "HRK",
    "hu": "HUF",
    "ie": "EUR",
    "it": "EUR",
    "lt": "EUR",
    "lu": "EUR",
    "lv": "EUR",
    "mt": "EUR",
    "nl": "EUR",
    "pl": "PLN",
    "pt": "EUR",
    "ro": "RON",
    "se": "SEK",
    "sl": "EUR",
    "sk": "EUR",
    "gb": "GBP",
    "tr": "TRY",
    "ch": "CHF",
    "li": "CHF",
    "us": "USD",
    "ca": "CAD",
    "kr": "KRW",
}


def get_vat_rate(country, postal_code: typing.Optional[str]):
    now = timezone.now()

    if now < VAT_RATES_PRE_2021_DATE:
        vat_rates = VAT_RATES_PRE_2021
    elif now < VAT_RATES_PRE_UK_REG_DATE:
        vat_rates = VAT_RATES_FROM_2021
    elif now < VAT_RATES_PRE_TR_REG_DATE:
        vat_rates = VAT_RATES_FROM_UK_REG
    elif now < VAT_RATES_PRE_KR_REG_DATE:
        vat_rates = VAT_RATES_FROM_TR_REG
    else:
        vat_rates = VAT_RATES_FROM_KR_REG

    if country == "es" and postal_code:
        postal_code_match = utils.spain_postcode_re.fullmatch(postal_code)
        if postal_code_match:
            postal_code_data = postal_code_match.groupdict()
            if postal_code_data["region"] in ("35", "38"):
                country = "ic"
            elif postal_code_data["region"] in ("51", "52"):
                country = "ea"

    return vat_rates.get(country.lower())


def need_billing_evidence():
    return False


def get_vies_country_code(iso_code: str):
    iso_code = iso_code.lower()
    if iso_code not in VAT_MOSS_COUNTRIES:
        return None
    elif iso_code == "gr":
        return "EL"
    else:
        return iso_code.upper()


class VerifyVATStatus(enum.Enum):
    OK = enum.auto()
    INVALID = enum.auto()
    ERROR = enum.auto()


@dataclasses.dataclass
class HMRCVATInfo:
    name: str
    address_line1: str
    address_line2: typing.Optional[str]
    address_line3: typing.Optional[str]
    address_line4: typing.Optional[str]
    address_line5: typing.Optional[str]
    post_code: typing.Optional[str]
    country_code: str
    consultation_number: typing.Optional[str]

    @classmethod
    def from_api_resp(cls, data: dict):
        return cls(
            name=data["target"]["name"],
            address_line1=data["target"]["address"]["line1"],
            address_line2=data["target"]["address"].get("line2"),
            address_line3=data["target"]["address"].get("line3"),
            address_line4=data["target"]["address"].get("line4"),
            address_line5=data["target"]["address"].get("line5"),
            post_code=data["target"]["address"].get("postcode"),
            country_code=data["target"]["address"].get("countryCode"),
            consultation_number=data.get("consultationNumber")
        )


def verify_vat_hmrc(number: str):
    hmrc_oauth_session = get_hmrc_session()
    if hmrc_oauth_session:
        hmrc_base_url = "https://test-api.service.hmrc.gov.uk" if settings.IS_TEST \
            else "https://api.service.hmrc.gov.uk"
        hmrc_url = f"{hmrc_base_url}/organisations/vat/check-vat-number/lookup/{number}"
        if settings.OWN_UK_VAT_ID:
            hmrc_url += f"/{settings.OWN_UK_VAT_ID}"
            
        headers = {
            "Accept": "application/vnd.hmrc.1.0+json",
            "Gov-Client-Connection-Method": "BATCH_PROCESS_DIRECT",
            "Gov-Client-User-IDs": "",
            "Gov-Client-Timezone": "UTC+00:00",
            "Gov-Client-User-Agent": "AS207960 Billing System",
            "Gov-Client-Local-IPs": "",
            "Gov-Client-MAC-Addresses": uuid.getnode().to_bytes(6, "big").hex(),
            "Gov-Vendor-Version": "",
            "Gov-Vendor-License-IDs": "",
        }

        try:
            resp = hmrc_oauth_session.get(hmrc_url, headers=headers)
        except (oauthlib.oauth2.rfc6749.errors.InvalidGrantError, oauthlib.oauth2.rfc6749.errors.TokenExpiredError):
            hmrc_oauth_session.fetch_token(
                token_url='https://test-api.service.hmrc.gov.uk/oauth/token' if settings.IS_TEST
                else 'https://api.service.hmrc.gov.uk/oauth/token',
                client_id=settings.HMRC_CLIENT_ID,
                client_secret=settings.HMRC_CLIENT_SECRET
            )
            resp = hmrc_oauth_session.get(hmrc_url, headers=headers)
        if resp.status_code == 404:
            return VerifyVATStatus.INVALID, None
        elif resp.status_code != 200:
            return VerifyVATStatus.ERROR, None

        resp_data = resp.json()
        return VerifyVATStatus.OK, HMRCVATInfo.from_api_resp(resp_data)
    else:
        return VerifyVATStatus.OK, None


def verify_vat_tr(number: str):
    if len(number) != 10:
        return False

    if not number.isdigit():
        return False

    s = 0
    for i, n in enumerate(reversed(number[:9]), 1):
        c1 = (int(n) + i) % 10
        if c1:
            c2 = (c1 * (2 ** i)) % 9 or 9
            s += c2

    check_digit = (10 - s) % 10
    last_digit = int(number[9])

    return check_digit == last_digit
