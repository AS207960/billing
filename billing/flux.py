from django.conf import settings
import requests_oauthlib
import oauthlib.oauth2
import stripe
import decimal
from . import models


def token_saver(token):
    # Not really sure what to do here, maybe save to DB but it's client auth so why bother?
    pass


if settings.DEBUG:
    oauth_client = oauthlib.oauth2.BackendApplicationClient(client_id=settings.FLUX_CLIENT_ID)
    oauth_session = requests_oauthlib.OAuth2Session(
        client_id=settings.FLUX_CLIENT_ID,
        client=oauth_client,
        auto_refresh_url='https://api.test.tryflux.com/auth/oauth/token',
        auto_refresh_kwargs={
            "client_id": settings.FLUX_CLIENT_ID,
            "client_secret": settings.FLUX_CLIENT_SECRET,
        },
        token_updater=token_saver
    )
    oauth_session.fetch_token(
        token_url="https://api.test.tryflux.com/auth/oauth/token",
        client_id=settings.FLUX_CLIENT_ID,
        client_secret=settings.FLUX_CLIENT_SECRET
    )


def send_charge_state_notif(charge_state: models.ChargeState):
    if not settings.DEBUG:
        return

    if charge_state.payment_ledger_item and charge_state.ledger_item:
        payment_methods = []
        items = []
        if charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_CARD:
            stripe_payment_intent = stripe.PaymentIntent.retrieve(charge_state.payment_ledger_item.type_id)
            for charge in stripe_payment_intent["charges"]["data"]:
                if not charge["paid"]:
                    continue
                if charge["payment_method_details"]["type"] == "card":
                    payment_methods.append({
                        "type": "CARD",
                        "amount": charge["amount"],
                        "card": {
                            "lastFour": charge["payment_method_details"]["card"]["last4"],
                            "authCode": charge["authorization_code"],
                            "scheme": charge["payment_method_details"]["card"]["network"].upper()
                        }
                    })
                else:
                    payment_methods.append({
                        "type": "CASH",
                        "amount": charge["amount"],
                    })
        else:
            payment_methods.append({
                "type": "CASH",
                "amount": int(charge_state.payment_ledger_item.amount * decimal.Decimal(100)),
            })

        items.append({
            "sku": str(charge_state.ledger_item.type_id),
            "description": charge_state.ledger_item.descriptor,
            "category": charge_state.ledger_item.type,
            "quantity": 1,
            "id": str(charge_state.ledger_item.type_id),
            "price": -int(charge_state.ledger_item.amount * decimal.Decimal(100)),
            "tax": -int(charge_state.ledger_item.amount * decimal.Decimal(20))
        })

        account_balance_amount = int(
            (charge_state.payment_ledger_item.amount + charge_state.ledger_item.amount) * decimal.Decimal(100)
        )
        if account_balance_amount != 0:
            items.append({
                "sku": "account_balance",
                "category": "charge",
                "description": "Paid from account balance",
                "quantity": 1,
                "price": account_balance_amount,
                "tax": 0
            })

        if not len(payment_methods):
            return

        request_data = {
            "id": str(charge_state.id),
            "merchantId": "as207960",
            "storeId": "online",
            "amount": -int(charge_state.payment_ledger_item.amount * decimal.Decimal(100)),
            "tax": -int(charge_state.ledger_item.amount * decimal.Decimal(20)),
            "currency": "GBP",
            "metadata": {
                "loyaltyCardId": charge_state.account.user.username
            },
            "transactionDate": charge_state.ledger_item.timestamp.isoformat('T'),
            "items": items,
            "payments": payment_methods,
            "status": "SETTLED" if charge_state.payment_ledger_item.state == models.LedgerItem.STATE_COMPLETED
            else "PENDING"
        }

        oauth_session.post("https://webhooks.test.tryflux.com/merchant", json=[
            request_data
        ])

