import base64
import binascii
import datetime
import decimal
import hmac
import re
import typing
import json

import cryptography.exceptions
import cryptography.hazmat.backends
import cryptography.hazmat.primitives.asymmetric.padding
import cryptography.hazmat.primitives.hashes
import cryptography.hazmat.primitives.serialization
import dateutil.parser
import schwifty
import stripe.error
import pywisetransfer
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .. import tasks, models, emails

transferwise_live_pub = cryptography.hazmat.primitives.serialization.load_der_public_key(
    base64.b64decode(
        b"MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAvO8vXV+JksBzZAY6GhSO"
        b"XdoTCfhXaaiZ+qAbtaDBiu2AGkGVpmEygFmWP4Li9m5+Ni85BhVvZOodM9epgW3F"
        b"bA5Q1SexvAF1PPjX4JpMstak/QhAgl1qMSqEevL8cmUeTgcMuVWCJmlge9h7B1CS"
        b"D4rtlimGZozG39rUBDg6Qt2K+P4wBfLblL0k4C4YUdLnpGYEDIth+i8XsRpFlogx"
        b"CAFyH9+knYsDbR43UJ9shtc42Ybd40Afihj8KnYKXzchyQ42aC8aZ/h5hyZ28yVy"
        b"Oj3Vos0VdBIs/gAyJ/4yyQFCXYte64I7ssrlbGRaco4nKF3HmaNhxwyKyJafz19e"
        b"HwIDAQAB",
    ),
    backend=cryptography.hazmat.backends.default_backend()
)
transferwise_sandbox_pub = cryptography.hazmat.primitives.serialization.load_der_public_key(
    base64.b64decode(
        b"MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwpb91cEYuyJNQepZAVfP"
        b"ZIlPZfNUefH+n6w9SW3fykqKu938cR7WadQv87oF2VuT+fDt7kqeRziTmPSUhqPU"
        b"ys/V2Q1rlfJuXbE+Gga37t7zwd0egQ+KyOEHQOpcTwKmtZ81ieGHynAQzsn1We3j"
        b"wt760MsCPJ7GMT141ByQM+yW1Bx+4SG3IGjXWyqOWrcXsxAvIXkpUD/jK/L958Cg"
        b"nZEgz0BSEh0QxYLITnW1lLokSx/dTianWPFEhMC9BgijempgNXHNfcVirg1lPSyg"
        b"z7KqoKUN0oHqWLr2U1A+7kqrl6O2nx3CKs1bj1hToT1+p4kcMoHXA7kA+VBLUpEs"
        b"VwIDAQAB",
    ),
    backend=cryptography.hazmat.backends.default_backend()
)
transferwise_swift_re = re.compile(
    r"^\((?P<bic>[A-Z0-9]+)\) (?P<iban>[A-Z0-9]+)$"
)
transferwise_fpid_re = re.compile(
    r"^\((?P<id>\w{20})(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})(?P<currency>\d{3})(?P<sort_code>\d{6})\)"
    r" (?P<account_number>\d{8})$"
)
wise_api = pywisetransfer.Client(
    api_key=settings.TRANSFERWISE_TOKEN,
    environment=settings.TRANSFERWISE_ENV,
    private_key_data=settings.TRANSFERWISE_PRIV_KEY.encode()
)


@csrf_exempt
@require_POST
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_ENDPOINT_SECRET
        )
    except ValueError:
        return HttpResponseBadRequest()
    except stripe.error.SignatureVerificationError:
        return HttpResponseBadRequest()

    with transaction.atomic():
        if event.type in ('payment_intent.succeeded', 'payment_intent.payment_failed', 'payment_intent.processing',
                          'payment_intent.canceled', 'payment_intent.requires_action'):
            payment_intent = event.data.object
            tasks.update_from_payment_intent(payment_intent)
        elif event.type in ('source.failed', 'source.chargeable', 'source.canceled'):
            source = event.data.object
            tasks.update_from_source(source)
        elif event.type in ('charge.pending', 'charge.succeeded', 'charge.failed', 'charge.succeeded',
                            'charge.refunded'):
            charge = event.data.object
            tasks.update_from_charge(charge)
        elif event.type == "charge.refund.updated":
            refund = event.data.object
            tasks.update_from_stripe_refund(refund)
        elif event.type in ("checkout.session.completed", "checkout.session.async_payment_failed",
                            "checkout.session.async_payment_succeeded"):
            session = event.data.object
            tasks.update_from_checkout_session(session)
        elif event.type == "setup_intent.succeeded":
            session = event.data.object
            tasks.setup_intent_succeeded(session)
        elif event.type == "cash_balance.funds_available":
            balance_transaction = event.data.object
            tasks.balance_funded(balance_transaction)
        elif event.type == "mandate.updated":
            mandate = event.data.object
            tasks.mandate_update(mandate)
        else:
            return HttpResponseBadRequest()

        return HttpResponse(status=200)


@csrf_exempt
@require_POST
def gc_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_WEBHOOK_SIGNATURE')

    own_sig = hmac.new(settings.GOCARDLESS_WEBHOOK_SECRET.encode(), payload, digestmod='sha256')
    own_digest = own_sig.hexdigest()

    if not hmac.compare_digest(sig_header, own_digest):
        return HttpResponseForbidden(status=498)

    try:
        events = json.loads(payload)
    except json.JSONDecodeError:
        return HttpResponseBadRequest()

    for event in events["events"]:
        with transaction.atomic():
            if event["resource_type"] == "payments":
                tasks.update_from_gc_payment(event["links"]["payment"], None)
            if event["resource_type"] == "billing_requests":
                tasks.update_from_gc_billing_request(event["links"]["billing_request"], None)
            elif event["resource_type"] == "mandates":
                scheme = event["details"].get("scheme")
                if scheme == "ach":
                    models.ACHMandate.sync_mandate(event["links"]["mandate"], None)
                elif scheme == "autogiro":
                    models.AutogiroMandate.sync_mandate(event["links"]["mandate"], None)
                elif scheme == "bacs":
                    models.GCBACSMandate.sync_mandate(event["links"]["mandate"], None)
                elif scheme == "becs":
                    models.BECSMandate.sync_mandate(event["links"]["mandate"], None)
                elif scheme == "becs_nz":
                    models.BECSNZMandate.sync_mandate(event["links"]["mandate"], None)
                elif scheme == "betalingsservice":
                    models.BetalingsserviceMandate.sync_mandate(event["links"]["mandate"], None)
                elif scheme == "pad":
                    models.PADMandate.sync_mandate(event["links"]["mandate"], None)
                elif scheme in ("sepa_core", "sepa_cor1"):
                    models.GCSEPAMandate.sync_mandate(event["links"]["mandate"], None)

    return HttpResponse(status=204)


def attempt_complete_bank_transfer(
        ref: typing.Optional[str], amount: decimal.Decimal, trans_account_data: dict, data=None,
        ledger_item=None, known_account=None, override_country_check: bool = False,
):
    found = False
    error = None

    if ref or ledger_item:
        if not ledger_item:
            normalised_ref = ref.upper().replace(" ", "").replace("\n", "")
            ledger_items = models.LedgerItem.objects.filter(
                type=models.LedgerItem.TYPE_BACS,
                state=models.LedgerItem.STATE_PENDING
            )
            for poss_ledger_item in ledger_items:
                if poss_ledger_item.type_id in normalised_ref:
                    ledger_item = poss_ledger_item
                    break

        if (trans_account_data or override_country_check) and ledger_item:
            if trans_account_data:
                known_account, _ = models.KnownBankAccount.objects.update_or_create(
                    account=ledger_item.account,
                    **trans_account_data
                )
            else:
                known_account = None

            if (
                    override_country_check
                    or not ledger_item.account.taxable
                    or ledger_item.evidence_billing_address.country_code.code.lower() == known_account.country_code.lower()
            ):
                ledger_item.amount = amount
                ledger_item.state = models.LedgerItem.STATE_COMPLETED
                if known_account:
                    ledger_item.evidence_bank_account = known_account
                ledger_item.save()
                found = True
            else:
                error = "Bank account country does not match billing address"

    if not found and (trans_account_data or known_account):
        if not known_account and trans_account_data:
            known_account = models.KnownBankAccount.objects.filter(
                **trans_account_data
            ).first()
        if known_account and known_account.account.billing_address and (
                known_account.account.billing_address.country_code.code.lower() == known_account.country_code.lower()
                or not known_account.account.taxable or override_country_check
        ):
            can_sell, can_sell_reason = known_account.account.can_sell
            if can_sell:
                new_ledger_item = models.LedgerItem(
                    account=known_account.account,
                    descriptor=f"Top-up by bank transfer: {ref}" if ref else "Top-up by bank transfer",
                    amount=amount,
                    charged_amount=amount,
                    country_code=known_account.account.billing_address.country_code.code.lower(),
                    type=models.LedgerItem.TYPE_BACS,
                    type_id=ref,
                    timestamp=timezone.now(),
                    state=models.LedgerItem.STATE_COMPLETED,
                    evidence_bank_account=known_account,
                    evidence_billing_address=known_account.account.billing_address
                )
                new_ledger_item.save()
                found = True
            else:
                error = can_sell_reason
        else:
            error = "Bank account country does not match billing address"

    if not found and data:
        text = json.dumps({
            "data": data,
            "error": error,
            "ledger_item": ledger_item.id if ledger_item else None,
            "known_account": known_account.id if known_account else None,
        }, indent=4, sort_keys=True)
        emails.send_email({
            "subject": "Unmatched Bank Transaction",
            "content": f"<p><pre>{text}</pre></p>",
        }, email="finance@as207960.net")

    return error


@csrf_exempt
@require_POST
def xfw_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_X_SIGNATURE_SHA256')
    is_test = request.META.get('HTTP_X_TEST_NOTIFICATION')

    try:
        xfw_sig = base64.b64decode(sig_header)
    except binascii.Error:
        return HttpResponseBadRequest()

    pubkey = transferwise_live_pub if settings.TRANSFERWISE_ENV == "live" else transferwise_sandbox_pub
    api_base = "https://api.transferwise.com" if settings.TRANSFERWISE_ENV == "live" else \
        "https://api.sandbox.transferwise.tech"

    try:
        pubkey.verify(
            xfw_sig,
            payload,
            cryptography.hazmat.primitives.asymmetric.padding.PKCS1v15(),
            cryptography.hazmat.primitives.hashes.SHA256()
        )
    except cryptography.exceptions.InvalidSignature:
        return HttpResponseForbidden()

    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return HttpResponseBadRequest()

    if is_test and is_test.lower() == "true":
        return HttpResponse(status=204)

    if event.get("event_type") == "balances#credit":
        profile_id = event["data"]["resource"]["profile_id"]
        account_id = event["data"]["resource"]["id"]
        credit_time = dateutil.parser.parse(event["data"]["occurred_at"])
        currency = event["data"]["currency"]
        amount = event["data"]["amount"]
        post_balance = event["data"]["post_transaction_balance_amount"]

        statement = wise_api.borderless_accounts.statement(
            profile_id=profile_id,
            account_id=account_id,
            currency=currency,
            interval_start=(credit_time - datetime.timedelta(seconds=5)).isoformat() + "Z",
            interval_end=(credit_time + datetime.timedelta(seconds=5)).isoformat() + "Z",
        )

        credit_transactions = filter(
            lambda t: t.type == "CREDIT" and t.details.type == "DEPOSIT",
            statement.transactions
        )
        found_t = None
        for t in credit_transactions:
            if t.amount.value == amount and t.runningBalance.value == post_balance:
                found_t = t
                break

        if found_t:
            sender_account = found_t.details.get("senderAccount")
            trans_account_data = None
            if sender_account:
                try:
                    trans_iban = sender_account
                    swift_match = transferwise_swift_re.match(sender_account)
                    if swift_match:
                        trans_iban = swift_match["iban"]

                    trans_iban = schwifty.IBAN(trans_iban)
                    trans_account_data = {
                        "country_code": trans_iban.country_code.lower(),
                        "bank_code": trans_iban.bank_code,
                        "branch_code": trans_iban.branch_code,
                        "account_code": trans_iban.account_code
                    }
                except ValueError:
                    fpid_match = transferwise_fpid_re.match(sender_account)
                    if fpid_match:
                        fpid_data = fpid_match.groupdict()
                        trans_account_data = {
                            "country_code": "gb",
                            "bank_code": "",
                            "branch_code": fpid_data["sort_code"],
                            "account_code": fpid_data["account_number"],
                        }

            amount = decimal.Decimal(found_t["amount"]["value"]) * \
                     models.ExchangeRate.get_rate(found_t["amount"]["currency"], "GBP")
            ref = found_t["details"].get("paymentReference")
            
            attempt_complete_bank_transfer(ref, amount, trans_account_data, data=found_t)

    return HttpResponse(status=204)


@csrf_exempt
@require_POST
def monzo_webhook(request, secret_key):
    if secret_key != settings.MONZO_WEBHOOK_SECRET_KEY:
        return HttpResponseForbidden()

    try:
        payload = json.loads(request.body)
    except ValueError:
        return HttpResponseBadRequest()

    if payload.get("type") == 'transaction.created':
        data = payload.get("data")
        ref = data.get("metadata", {}).get("notes")
        amount = decimal.Decimal(data.get("amount")) / decimal.Decimal(100)
        if amount > 0:
            trans_account_data = None

            if "counterparty" in data:
                if "iban" in data["counterparty"]:
                    try:
                        trans_iban = schwifty.IBAN(data["counterparty"]["iban"])
                        trans_account_data = {
                            "country_code": trans_iban.country_code.lower(),
                            "bank_code": trans_iban.bank_code,
                            "branch_code": trans_iban.branch_code,
                            "account_code": trans_iban.account_code
                        }
                    except ValueError:
                        pass
                elif "account_number" in data["counterparty"] and "sort_code" in data["counterparty"]:
                    trans_account_data = {
                        "country_code": "gb",
                        "bank_code": "",
                        "branch_code": data["counterparty"]["sort_code"],
                        "account_code": data["counterparty"]["account_number"],
                    }

            attempt_complete_bank_transfer(ref, amount, trans_account_data, data=data)

    else:
        return HttpResponseBadRequest()

    return HttpResponse(status=200)

@csrf_exempt
@require_POST
def coinbase_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_X_CC_WEBHOOK_SIGNATURE')

    own_sig = hmac.new(settings.COINBASE_WEBHOOK_SECRET.encode(), payload, digestmod='sha256')
    own_digest = own_sig.hexdigest()

    if not hmac.compare_digest(sig_header, own_digest):
        return HttpResponseForbidden(status=498)

    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return HttpResponseBadRequest()

    if event["event"]["type"].startswith("charge:"):
        tasks.update_from_coinbase_charge(event["event"]["data"])
    else:
        return HttpResponseBadRequest()

    return HttpResponse(status=200)