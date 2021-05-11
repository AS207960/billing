import datetime
import decimal

import oauthlib.oauth2
import requests_oauthlib
import stripe
from django.conf import settings

from . import models


def token_saver(token):
    # Not really sure what to do here, maybe save to DB but it's client auth so why bother?
    pass


oauth_client = oauthlib.oauth2.BackendApplicationClient(client_id=settings.FLUX_CLIENT_ID)
oauth_session = requests_oauthlib.OAuth2Session(
    client_id=settings.FLUX_CLIENT_ID,
    client=oauth_client,
    # auto_refresh_url='https://api.test.tryflux.com/auth/oauth/token' if settings.IS_TEST
    # else 'https://api.tryflux.com/auth/oauth/token',
    # auto_refresh_kwargs={
    #     "client_id": settings.FLUX_CLIENT_ID,
    #     "client_secret": settings.FLUX_CLIENT_SECRET,
    # },
    # token_updater=token_saver
)
oauth_session.fetch_token(
    token_url='https://api.test.tryflux.com/auth/oauth/token' if settings.IS_TEST
    else 'https://api.tryflux.com/auth/oauth/token',
    client_id=settings.FLUX_CLIENT_ID,
    client_secret=settings.FLUX_CLIENT_SECRET
)


def send_charge_state_notif(charge_state: models.ChargeState):
    if charge_state.payment_ledger_item and charge_state.ledger_item:
        payment_methods = []
        items = []

        vat_charged = charge_state.ledger_item.amount * charge_state.ledger_item.vat_rate
        from_account_balance = charge_state.ledger_item.amount + charge_state.payment_ledger_item.amount
        charged_amount = charge_state.payment_ledger_item.amount

        if charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_CARD:
            stripe_payment_intent = stripe.PaymentIntent.retrieve(charge_state.payment_ledger_item.type_id)
            for charge in stripe_payment_intent["charges"]["data"]:
                if not charge["paid"]:
                    continue
                if charge["payment_method_details"]["type"] == "card":
                    payment_methods.append({
                        "type": "CARD",
                        "timestamp": charge_state.payment_ledger_item.completed_timestamp.isoformat("T"),
                        "method": charge_state.payment_ledger_item.descriptor,
                        "amount": int(round(charged_amount * decimal.Decimal(100))),
                        "card": {
                            "lastFour": charge["payment_method_details"]["card"]["last4"],
                            "authCode": charge["authorization_code"],
                            "scheme": charge["payment_method_details"]["card"]["network"].upper()
                        }
                    })
                else:
                    payment_methods.append({
                        "type": "CHEQUE",
                        "timestamp": charge_state.payment_ledger_item.completed_timestamp.isoformat("T"),
                        "amount": int(round(charged_amount * decimal.Decimal(100))),
                        "method": charge_state.payment_ledger_item.descriptor
                    })
        else:
            payment_methods.append({
                "type": "CHEQUE",
                "timestamp": charge_state.payment_ledger_item.completed_timestamp.isoformat("T"),
                "amount": int(round(charged_amount * decimal.Decimal(100))),
                "method": charge_state.payment_ledger_item.descriptor
            })

        items.append({
            "sku": str(charge_state.ledger_item.id),
            "description": charge_state.ledger_item.descriptor,
            "category": charge_state.ledger_item.type,
            "quantity": 1,
            "id": str(charge_state.ledger_item.id),
            "price": -int(round(charge_state.ledger_item.amount * decimal.Decimal(100))),
            "tax": 0
        })

        if from_account_balance != 0:
            items.append({
                "sku": "account_balance",
                "category": "charge",
                "description": "Paid from account balance",
                "quantity": 1,
                "price": int(round(from_account_balance * decimal.Decimal(100))),
                "tax": 0
            })

        if not len(payment_methods):
            return

        request_data = {
            "id": str(charge_state.id),
            "merchantId": "as207960",
            "storeId": "online",
            "storeName": "Glauca Digital Online",
            "address": {
                "streetAddress": "13 Pen-y-lan Terrace",
                "locality": "Penylan",
                "region": "Cardiff",
                "postalCode": "CF23 9EU",
                "country": "Wales",
                "sovereign": "The United Kingdom"
            },
            "amount": int(round(charged_amount * decimal.Decimal(100))),
            "tax": int(round(vat_charged * decimal.Decimal(100))),
            "currency": "GBP",
            "metadata": {
                "loyaltyCardId": charge_state.account.user.username
            },
            "transactionDate": charge_state.ledger_item.timestamp.astimezone(datetime.timezone.utc).isoformat('T'),
            "items": items,
            "payments": payment_methods,
            "status":
                "SETTLED" if
                (
                    charge_state.payment_ledger_item if charge_state.payment_ledger_item else charge_state.ledger_item
                ).state == models.LedgerItem.STATE_COMPLETED else "PENDING"
        }

        hook_url = "https://webhooks.test.tryflux.com/merchant" if settings.IS_TEST \
            else "https://webhooks.tryflux.com/merchant"
        try:
            oauth_session.post(hook_url, json=[request_data])
        except (oauthlib.oauth2.rfc6749.errors.InvalidGrantError, oauthlib.oauth2.rfc6749.errors.TokenExpiredError):
            oauth_session.fetch_token(
                token_url='https://api.test.tryflux.com/auth/oauth/token' if settings.IS_TEST
                else 'https://api.tryflux.com/auth/oauth/token',
                client_id=settings.FLUX_CLIENT_ID,
                client_secret=settings.FLUX_CLIENT_SECRET
            )
            oauth_session.post(hook_url, json=[request_data])
