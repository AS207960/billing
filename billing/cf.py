import enum
import dataclasses
import typing
import requests
from django.conf import settings
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from . import models

class CloudflareResult(enum.Enum):
    SUCCESS = enum.auto()
    FAILURE = enum.auto()
    NEEDS_SETUP = enum.auto()

@dataclasses.dataclass
class CloudflareAccount:
    account_id: typing.Optional[str]
    result: CloudflareResult
    message: typing.Optional[str] = None


def setup_cloudflare_account(account: models.Account):
    if account.cloudflare_account_id:
        return CloudflareAccount(account_id=account.cloudflare_account_id, result=CloudflareResult.SUCCESS)

    if not account.billing_address:
        return CloudflareAccount(account_id=None, result=CloudflareResult.NEEDS_SETUP)

    can_sell, can_sell_message = account.can_sell
    if not can_sell:
        return CloudflareAccount(account_id=None, result=CloudflareResult.FAILURE, message=can_sell_message)

    if account.billing_address.organisation:
        account_name = account.billing_address.organisation
    else:
        account_name = f"{account.user.first_name} {account.user.last_name}"

    r = requests.post("https://api.cloudflare.com/client/v4/accounts", headers={
        "X-Auth-Email": settings.CLOUDFLARE_API_EMAIL,
        "X-Auth-Key": settings.CLOUDFLARE_API_KEY,
    }, json={
        "name": account_name,
        "type": "standard",
        "business_name": account_name,
        "business_email": account.user.email,
        "business_address": account.billing_address.formatted,
    })

    if r.status_code != 200:
        return CloudflareAccount(account_id=None, result=CloudflareResult.FAILURE, message="Failed to create account")

    data = r.json()

    account.cloudflare_account_id = data["result"]["id"]
    account.save()

    set_cloudflare_ns(account)
    add_cloudflare_user(account)

    return CloudflareAccount(account_id=account.cloudflare_account_id, result=CloudflareResult.SUCCESS)

def set_cloudflare_ns(account: models.Account):
    if not account.cloudflare_account_id:
        return

    r = requests.put(f"https://api.cloudflare.com/client/v4/accounts/{account.cloudflare_account_id}", headers={
        "X-Auth-Email": settings.CLOUDFLARE_API_EMAIL,
        "X-Auth-Key": settings.CLOUDFLARE_API_KEY,
    }, json={
        "settings": {
            "default_nameservers": "custom.tenant"
        }
    })
    r.raise_for_status()

def delete_cloudflare_account(account: models.Account):
    if not account.cloudflare_account_id:
        return

    r = requests.delete(f"https://api.cloudflare.com/client/v4/accounts/{account.cloudflare_account_id}", headers={
        "X-Auth-Email": settings.CLOUDFLARE_API_EMAIL,
        "X-Auth-Key": settings.CLOUDFLARE_API_KEY,
    })
    r.raise_for_status()

    account.cloudflare_account_id = None
    account.save()

@receiver(pre_delete, sender=models.Account)
def account_delete_handler(_sender, **kwargs):
    delete_cloudflare_account(kwargs["instance"])


def add_cloudflare_user(account: models.Account):
    if not account.cloudflare_account_id:
        return

    r = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{account.cloudflare_account_id}/members", headers={
        "X-Auth-Email": settings.CLOUDFLARE_API_EMAIL,
        "X-Auth-Key": settings.CLOUDFLARE_API_KEY,
    }, json={
        "email": account.user.email,
        "roles": ["05784afa30c1afe1440e79d9351c7430"],
    })
    r.raise_for_status()