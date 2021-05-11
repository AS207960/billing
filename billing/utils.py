import ipaddress
import re

canada_postcode_re = re.compile(r"^(?P<district>[ABCEGHJKLMNPRSTVXY])[0-9][A-Z] ?[0-9][A-Z][0-9]$")
uk_postcode_re = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$")
spain_postcode_re = re.compile(r"^(?P<region>[0-9]{2})[0-9]{3}$")
germany_postcode_re = re.compile(r"^(?P<area>[0-9]{2})(?P<district>[0-9]{3})$")
france_postcode_re = re.compile(r"^(?P<department>[0-9]{2})[0-9]{3}$")


def get_ip(request):
    net64_net = ipaddress.IPv6Network("2a0d:1a40:7900:6::/80")
    addr = ipaddress.ip_address(request.META['REMOTE_ADDR'])
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        if addr in net64_net:

            addr = ipaddress.IPv4Address(addr._ip & 0xFFFFFFFF)
    return addr


def country_from_stripe_payment_method(payment_method):
    if payment_method["type"] == "card":
        return payment_method["card"]["country"].lower()
    elif payment_method["type"] == "au_becs_debit":
        return "au"
    elif payment_method["type"] == "bacs_debit":
        return "gb"
    elif payment_method["type"] == "bancontact":
        return "be"
    elif payment_method["type"] == "eps":
        return "at"
    elif payment_method["type"] == "fps":
        return "my"
    elif payment_method["type"] == "giropay":
        return "de"
    elif payment_method["type"] == "ideal":
        return "nl"
    elif payment_method["type"] == "oxxo":
        return "mx"
    elif payment_method["type"] == "p24":
        return "pl"
    elif payment_method["type"] == "sepa_debit":
        return payment_method["sepa_debit"]["country"].lower()
    elif payment_method["type"] == "sofort":
        return payment_method["sofort"]["country"].lower()
    elif payment_method["type"] == "customer_balance":
        return "gb"


def descriptor_from_stripe_payment_method(payment_method):
    if payment_method["type"] == "card":
        return f'{payment_method["card"]["brand"].upper()} ending {payment_method["card"]["last4"]}'
    elif payment_method["type"] == "giropay":
        return "account with GIROPAY"
    elif payment_method["type"] == "sofort":
        return "account with Sofort"
    elif payment_method["type"] == "ideal":
        return "account with iDEAL"
    elif payment_method["type"] == "customer_balance":
        return "bank account"
    elif payment_method["type"] == "p24":
        if payment_method["p24"].get("bank"):
            return f'account with {payment_method["bank"]}'
        else:
            return "account"
    elif payment_method["type"] == "sepa_debit":
        return f'accounting ending {payment_method["sepa_debit"]["last4"]}'
    else:
        return "payment method"
