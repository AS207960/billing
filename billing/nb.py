import typing
import requests
from django.conf import settings
from django.db.models.signals import pre_delete, post_save
from django.dispatch import receiver
from . import models, tasks


def setup_netbox_account(account: models.Account) -> typing.Optional[int]:
    if account.netbox_account_id:
        return account.netbox_account_id

    r = requests.get(f"https://nb.as207960.net/api/tenancy/tenants/", headers={
        "Authorization": f"Token {settings.NETBOX_API_TOKEN}",
    }, params={
        "cf_user_id": account.user.username,
    })
    data = r.json()
    if data["results"]:
        account.netbox_account_id = data["results"][0]["id"]
        account.save()
        return account.netbox_account_id

    if account.billing_address.organisation:
        account_name = account.billing_address.organisation
    else:
        account_name = f"{account.user.first_name} {account.user.last_name}"

    r = requests.post("https://nb.as207960.net/api/tenancy/tenants/", headers={
        "Authorization": f"Token {settings.NETBOX_API_TOKEN}",
    }, json={
        "name": account_name,
        "custom_fields": {
            "user_id": account.user.username,
        }
    })

    if r.status_code != 200:
        return None

    data = r.json()

    account.netbox_account_id = data["id"]
    account.save()

    return account.netbox_account_id


def delete_netbox_account(account: models.Account):
    if not account.netbox_account_id:
        return

    r = requests.delete(f"https://nb.as207960.net/api/tenancy/tenants/{account.netbox_account_id}", headers={
        "Authorization": f"Token {settings.NETBOX_API_TOKEN}",
    })
    r.raise_for_status()

    account.netbox_account_id = None
    account.save()


@tasks.as_thread
def update_netbox_account_name(account: models.Account):
    if not account.netbox_account_id:
        return

    if account.billing_address.organisation:
        account_name = account.billing_address.organisation
    else:
        account_name = f"{account.user.first_name} {account.user.last_name}"

    r = requests.patch(f"https://nb.as207960.net/api/tenancy/tenants/{account.netbox_account_id}", headers={
        "Authorization": f"Token {settings.NETBOX_API_TOKEN}",
    }, json={
        "name": account_name,
    })
    r.raise_for_status()


@receiver(pre_delete, sender=models.Account)
def account_delete_handler(_sender, instance: models.Account, **kwargs):
    delete_netbox_account(instance)

@receiver(post_save, sender=models.Account)
def send_charge_state_notif_receiver(instance: models.Account, **kwargs):
    update_netbox_account_name(instance)

@receiver(post_save, sender=models.AccountBillingAddress)
def send_charge_state_notif_receiver(instance: models.AccountBillingAddress, **kwargs):
    if instance.account.billing_address == instance:
        update_netbox_account_name(instance.account)