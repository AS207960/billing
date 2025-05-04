from django import template
from .. import models

register = template.Library()

@register.filter
def can_use_payment_country(account: models.Account, country: str):
    return account.can_use_payment_country(country)
