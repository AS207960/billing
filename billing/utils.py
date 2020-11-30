import ipaddress


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