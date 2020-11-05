import csv
import datetime
import decimal
import json
import secrets
import uuid
import ipaddress
import hmac

import django_keycloak_auth.clients
import keycloak.exceptions
import stripe
import stripe.error
import gocardless_pro.errors
import schwifty
import requests
import binascii
import dateutil.parser
import base64
import cryptography.hazmat.primitives.serialization
import cryptography.hazmat.primitives.hashes
import cryptography.hazmat.primitives.asymmetric.padding
import cryptography.exceptions
import cryptography.hazmat.backends
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.models import DecimalField, OuterRef, Q, Sum, Subquery
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, Http404
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.template import loader
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from idempotency_key.decorators import idempotency_key

from . import forms, models, tasks

gocardless_client = gocardless_pro.Client(access_token=settings.GOCARDLESS_TOKEN, environment=settings.GOCARDLESS_ENV)
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


def get_ip(request):
    net64_net = ipaddress.IPv6Network("2a0d:1a40:7900:6::/80")
    addr = ipaddress.ip_address(request.META['REMOTE_ADDR'])
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        if addr in net64_net:
            addr = ipaddress.IPv4Address(addr._ip & 0xFFFFFFFF)
    return addr


def sw(request):
    return render(request, "billing/js/sw.js", {}, content_type="application/javascript")


@login_required
def dashboard(request):
    ledger_items = models.LedgerItem.objects.filter(account=request.user.account)

    return render(request, "billing/dashboard.html", {
        "ledger_items": ledger_items,
        "account": request.user.account
    })


@login_required
def top_up(request):
    if request.method == "POST":
        form = forms.TopUpForm(request.POST)
        if form.is_valid():
            if "charge_state_id" in request.session:
                request.session.pop("charge_state_id")
            request.session["amount"] = str(form.cleaned_data["amount"])

            if "ach_mandate" in request.POST:
                return redirect('top_up_existing_ach_direct_debit', mandate_id=request.POST["ach_mandate"])
            if "autogiro_mandate" in request.POST:
                return redirect('top_up_existing_autogiro_direct_debit', mandate_id=request.POST["autogiro_mandate"])
            if "bacs_mandate" in request.POST:
                return redirect('top_up_existing_bacs_direct_debit', mandate_id=request.POST["bacs_mandate"])
            if "becs_mandate" in request.POST:
                return redirect('top_up_existing_becs_direct_debit', mandate_id=request.POST["becs_mandate"])
            if "becs_nz_mandate" in request.POST:
                return redirect('top_up_existing_becs_nz_direct_debit', mandate_id=request.POST["becs_nz_mandate"])
            if "betalingsservice_mandate" in request.POST:
                return redirect('top_up_existing_betalingsservice_direct_debit',
                                mandate_id=request.POST["betalingsservice_mandate"])
            if "pad_mandate" in request.POST:
                return redirect('top_up_existing_pad_direct_debit', mandate_id=request.POST["pad_mandate"])
            if "sepa_mandate" in request.POST:
                return redirect('top_up_existing_sepa_direct_debit', mandate_id=request.POST["sepa_mandate"])
            if "card" in request.POST:
                return redirect('top_up_existing_card', card_id=request.POST["card"])

            if form.cleaned_data['method'] == forms.TopUpForm.METHOD_CARD:
                return redirect("top_up_card")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_BACS:
                return redirect("top_up_bacs")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_SOFORT:
                return redirect("top_up_sofort")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_GIROPAY:
                return redirect("top_up_giropay")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_BANCONTACT:
                return redirect("top_up_bancontact")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_EPS:
                return redirect("top_up_eps")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_IDEAL:
                return redirect("top_up_ideal")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_MULTIBANCO:
                return redirect("top_up_multibanco")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_P24:
                return redirect("top_up_p24")

    account = request.user.account  # type: models.Account
    cards = []

    if account.stripe_customer_id:
        cards = list(stripe.PaymentMethod.list(
            customer=account.stripe_customer_id,
            type="card"
        ).auto_paging_iter())

    def map_sepa_mandate(m):
        mandate = stripe.Mandate.retrieve(m.mandate_id)
        payment_method = stripe.PaymentMethod.retrieve(mandate["payment_method"])
        return {
            "id": m.id,
            "cc": payment_method["sepa_debit"]["country"],
            "last4": payment_method["sepa_debit"]["last4"],
            "bank": payment_method["sepa_debit"]["bank_code"],
            "ref": mandate["payment_method_details"]["sepa_debit"]["reference"],
        }

    def map_bacs_mandate(m):
        mandate = stripe.Mandate.retrieve(m.mandate_id)
        payment_method = stripe.PaymentMethod.retrieve(mandate["payment_method"])
        return {
            "id": m.id,
            "last4": payment_method["bacs_debit"]["last4"],
            "bank": payment_method["bacs_debit"]["sort_code"],
            "ref": mandate["payment_method_details"]["bacs_debit"]["reference"],
        }

    def map_ach_mandate(m):
        mandate = gocardless_client.mandates.get(m.mandate_id)
        bank_account = gocardless_client.customer_bank_accounts.get(mandate.links.customer_bank_account)
        return {
            "id": m.id,
            "last4": bank_account.account_number_ending,
            "account_type": bank_account.account_type,
            "bank": bank_account.bank_name,
            "ref": mandate.reference,
        }

    def map_gc_bacs_mandate(m):
        mandate = gocardless_client.mandates.get(m.mandate_id)
        bank_account = gocardless_client.customer_bank_accounts.get(mandate.links.customer_bank_account)
        return {
            "id": m.id,
            "last4": bank_account.account_number_ending,
            "bank": bank_account.bank_name,
            "ref": mandate.reference,
        }

    def map_gc_sepa_mandate(m):
        mandate = gocardless_client.mandates.get(m.mandate_id)
        bank_account = gocardless_client.customer_bank_accounts.get(mandate.links.customer_bank_account)
        return {
            "id": m.id,
            "cc": bank_account.country_code,
            "last4": bank_account.account_number_ending,
            "bank": bank_account.bank_name,
            "ref": mandate.reference,
        }

    ach_mandates = list(map(map_ach_mandate, models.ACHMandate.objects.filter(account=account, active=True)))
    autogiro_mandates = list(
        map(map_gc_bacs_mandate, models.AutogiroMandate.objects.filter(account=account, active=True)))
    bacs_mandates = list(map(map_bacs_mandate, models.BACSMandate.objects.filter(account=account, active=True)))
    bacs_mandates += list(map(map_gc_bacs_mandate, models.GCBACSMandate.objects.filter(account=account, active=True)))
    becs_mandates = list(map(map_gc_bacs_mandate, models.BECSMandate.objects.filter(account=account, active=True)))
    becs_nz_mandates = list(map(map_gc_bacs_mandate, models.BECSNZMandate.objects.filter(account=account, active=True)))
    betalingsservice_mandates = list(
        map(map_gc_bacs_mandate, models.BetalingsserviceMandate.objects.filter(account=account, active=True)))
    pad_mandates = list(map(map_gc_bacs_mandate, models.PADMandate.objects.filter(account=account, active=True)))
    sepa_mandates = list(map(map_sepa_mandate, models.SEPAMandate.objects.filter(account=account, active=True)))
    sepa_mandates += list(map(map_gc_sepa_mandate, models.GCSEPAMandate.objects.filter(account=account, active=True)))

    return render(request, "billing/top_up.html", {
        "cards": cards,
        "ach_mandates": ach_mandates,
        "autogiro_mandates": autogiro_mandates,
        "bacs_mandates": bacs_mandates,
        "becs_mandates": becs_mandates,
        "becs_nz_mandates": becs_nz_mandates,
        "betalingsservice_mandates": betalingsservice_mandates,
        "pad_mandates": pad_mandates,
        "sepa_mandates": sepa_mandates
    })


@login_required
def top_up_card(request):
    account = request.user.account

    if "charge_state_id" in request.session:
        charge_state = get_object_or_404(models.ChargeState, id=request.session["charge_state_id"])
    else:
        charge_state = None

    if request.method != "POST":
        return render(request, "billing/top_up_card.html", {
            "is_new": False
        })
    else:
        if "amount" not in request.session:
            return redirect("top_up")
        amount = decimal.Decimal(request.session.pop("amount"))
        charge_currency = request.POST.get("currency")
        if charge_currency not in ("eur", "gbp", "usd", "aud", "nzd", "sgd", "ron"):
            return HttpResponseBadRequest()

        amount_currency = models.ExchangeRate.get_rate('gbp', charge_currency) * amount
        amount_int = int(amount_currency * decimal.Decimal(100))
        payment_intent = stripe.PaymentIntent.create(
            amount=amount_int,
            currency=charge_currency,
            customer=account.get_stripe_id(),
            description='Top-up',
            receipt_email=request.user.email,
            setup_future_usage='off_session',
            statement_descriptor_suffix="Top-up",
            payment_method_options={
                "card": {
                    "request_three_d_secure": "any"
                }
            }
        )

        ledger_item = models.LedgerItem(
            account=account,
            descriptor="Top-up by card",
            amount=amount,
            type=models.LedgerItem.TYPE_CARD,
            type_id=payment_intent['id']
        )
        ledger_item.save()
        if charge_state:
            charge_state.payment_ledger_item = ledger_item
            charge_state.save()
            redirect_uri = reverse('complete_charge', args=(charge_state.id,))
        else:
            redirect_uri = reverse('dashboard')

        return render(request, "billing/top_up_card.html", {
            "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
            "client_secret": payment_intent["client_secret"],
            "customer_name": f"{request.user.first_name} {request.user.last_name}",
            "amount": amount_int,
            "currency": charge_currency,
            "redirect_uri": redirect_uri,
            "is_new": True
        })


@login_required
def top_up_existing_card(request, card_id):
    account = request.user.account

    if "charge_state_id" in request.session:
        charge_state = get_object_or_404(models.ChargeState, id=request.session["charge_state_id"])
    else:
        charge_state = None

    if request.method != "POST":
        return render(request, "billing/top_up_card.html", {
            "is_new": False
        })
    else:
        if "amount" not in request.session:
            return redirect("top_up")
        amount = decimal.Decimal(request.session.pop("amount"))
        charge_currency = request.POST.get("currency")
        if charge_currency not in ("eur", "gbp", "usd", "aud", "nzd", "sgd", "ron"):
            return HttpResponseBadRequest()

        amount_currency = models.ExchangeRate.get_rate('gbp', charge_currency) * amount
        amount_int = int(amount_currency * decimal.Decimal(100))
        payment_method = stripe.PaymentMethod.retrieve(card_id)

        if payment_method['customer'] != request.user.account.stripe_customer_id:
            return HttpResponseForbidden()

        redirect_uri = reverse('complete_charge', args=(charge_state.id,)) if charge_state else reverse('dashboard')

        payment_intent = stripe.PaymentIntent.create(
            amount=amount_int,
            currency=charge_currency,
            customer=account.get_stripe_id(),
            description='Top-up',
            receipt_email=request.user.email,
            statement_descriptor_suffix="Top-up",
            payment_method=card_id,
            confirm=True,
            return_url=request.build_absolute_uri(redirect_uri)
        )

        ledger_item = models.LedgerItem(
            account=account,
            descriptor="Top-up by card",
            amount=amount,
            type=models.LedgerItem.TYPE_CARD,
            type_id=payment_intent['id']
        )
        ledger_item.save()
        if charge_state:
            charge_state.payment_ledger_item = ledger_item
            charge_state.save()

        if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
            return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])

        return redirect(redirect_uri)


@login_required
def complete_top_up_card(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden

    if ledger_item.state != ledger_item.STATE_PENDING:
        return redirect('dashboard')

    if ledger_item.type != ledger_item.TYPE_CARD:
        return HttpResponseBadRequest

    payment_intent = stripe.PaymentIntent.retrieve(ledger_item.type_id)

    if payment_intent["status"] == "succeeded":
        ledger_item.state = ledger_item.STATE_COMPLETED
        ledger_item.save()
        return redirect('dashboard')

    return render(request, "billing/top_up_card.html", {
        "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
        "client_secret": payment_intent["client_secret"],
        "customer_name": f"{request.user.first_name} {request.user.last_name}",
        "amount": payment_intent['amount'],
        "currency": payment_intent['currency'],
        "is_new": True
    })


@login_required
def top_up_bacs(request):
    if request.method == "POST":
        if "iban" in request.POST:
            try:
                iban = schwifty.IBAN(request.POST.get("iban"))
            except ValueError as e:
                return render(request, "billing/top_up_bacs_search.html", {
                    "iban": request.POST.get("iban"),
                    "iban_error": str(e)
                })

            try:
                lookup = gocardless_client.bank_details_lookups.create(params={
                    "iban": iban.compact
                })
                if not len(lookup.available_debit_schemes):
                    return render(request, "billing/top_up_bacs_no_schemes.html")
                else:
                    return render(request, "billing/top_up_bacs_schemes.html", {
                        "bank_name": lookup.bank_name,
                        "schemes": lookup.available_debit_schemes
                    })
            except gocardless_pro.errors.ValidationFailedError as e:
                return render(request, "billing/top_up_bacs_no_schemes.html")

    return render(request, "billing/top_up_bacs_search.html")


@login_required
def top_up_bacs_local(request, country):
    country = country.lower()

    if country == "gb":
        country_name = "United Kingdom"
        form_c = forms.GBBankAccountForm
    elif country == "au":
        country_name = "Australian"
        form_c = forms.AUBankAccountForm
    elif country == "at":
        country_name = "Austrian"
        form_c = forms.ATBankAccountForm
    elif country == "be":
        country_name = "Belgium"
        form_c = forms.BEBankAccountForm
    elif country == "ca":
        country_name = "Canadian"
        form_c = forms.CABankAccountForm
    elif country == "cy":
        country_name = "Cyprus"
        form_c = forms.CYBankAccountForm
    elif country == "dk":
        country_name = "Danish"
        form_c = forms.DKBankAccountForm
    elif country == "ee":
        country_name = "Estonian"
        form_c = forms.EEBankAccountForm
    elif country == "fi":
        country_name = "Finnish"
        form_c = forms.FIBankAccountForm
    elif country == "fr":
        country_name = "French"
        form_c = forms.FRBankAccountForm
    elif country == "de":
        country_name = "German"
        form_c = forms.DEBankAccountForm
    elif country == "gr":
        country_name = "Greek"
        form_c = forms.GRBankAccountForm
    elif country == "ie":
        country_name = "Irish"
        form_c = forms.IEBankAccountForm
    elif country == "it":
        country_name = "Italian"
        form_c = forms.ITBankAccountForm
    elif country == "lv":
        country_name = "Latvian"
        form_c = forms.LVBankAccountForm
    elif country == "lt":
        country_name = "Lithuanian"
        form_c = forms.LTBankAccountForm
    elif country == "lu":
        country_name = "Luxembourg"
        form_c = forms.LUBankAccountForm
    elif country == "mt":
        country_name = "Maltese"
        form_c = forms.MTBankAccountForm
    elif country == "mc":
        country_name = "Monaco"
        form_c = forms.MCBankAccountForm
    elif country == "nl":
        country_name = "Netherlands"
        form_c = forms.NLBankAccountForm
    elif country == "nz":
        country_name = "New Zealand"
        form_c = forms.NZBankAccountForm
    elif country == "pt":
        country_name = "Portuguese"
        form_c = forms.PTBankAccountForm
    elif country == "sm":
        country_name = "San Marino"
        form_c = forms.SMBankAccountForm
    elif country == "sk":
        country_name = "Slovakian"
        form_c = forms.SKBankAccountForm
    elif country == "si":
        country_name = "Slovenian"
        form_c = forms.SIBankAccountForm
    elif country == "es":
        country_name = "Spanish"
        form_c = forms.ESBankAccountForm
    elif country == "se":
        country_name = "Swedish"
        form_c = forms.SEBankAccountForm
    elif country == "us":
        country_name = "United States"
        form_c = forms.USBankAccountForm
    else:
        raise Http404()

    if request.method == "POST":
        form = form_c(request.POST)
        if form.is_valid():
            account_data = form.cleaned_data
            account_data["country_code"] = country.upper()
            if "account_type" in account_data:
                request.session["dd_account_type"] = account_data["account_type"]
                del account_data["account_type"]
            try:
                lookup = gocardless_client.bank_details_lookups.create(params=account_data)
                if not len(lookup.available_debit_schemes):
                    return render(request, "billing/top_up_bacs_no_schemes.html", {
                        "country_code": country
                    })
                else:
                    return render(request, "billing/top_up_bacs_schemes.html", {
                        "bank_name": lookup.bank_name,
                        "schemes": lookup.available_debit_schemes,
                        "country_code": country
                    })
            except gocardless_pro.errors.ValidationFailedError as e:
                if e.errors:
                    for error in e.errors:
                        form.add_error(error['field'], "Invalid value")
                else:
                    form.add_error(None, e.message)
    else:
        form = form_c()

    return render(request, "billing/top_up_bacs_search_local.html", {
        "country": country_name,
        "form": form
    })


def show_bank_details(request, amount, ref, currency):
    if currency == "gbp":
        return render(request, "billing/top_up_bank_gbp.html", {
            "amount": amount,
            "ref": ref
        })
    elif currency == "usd":
        amount = models.ExchangeRate.get_rate('gbp', 'usd') * amount
        return render(request, "billing/top_up_bank_usd.html", {
            "amount": amount,
            "ref": ref
        })
    elif currency == "eur":
        amount = models.ExchangeRate.get_rate('gbp', 'eur') * amount
        return render(request, "billing/top_up_bank_eur.html", {
            "amount": amount,
            "ref": ref
        })
    elif currency == "aud":
        amount = models.ExchangeRate.get_rate('gbp', 'aud') * amount
        return render(request, "billing/top_up_bank_aud.html", {
            "amount": amount,
            "ref": ref
        })
    elif currency == "nzd":
        amount = models.ExchangeRate.get_rate('gbp', 'nzd') * amount
        return render(request, "billing/top_up_bank_nzd.html", {
            "amount": amount,
            "ref": ref
        })
    elif currency == "huf":
        amount = models.ExchangeRate.get_rate('gbp', 'huf') * amount
        return render(request, "billing/top_up_bank_huf.html", {
            "amount": amount,
            "ref": ref
        })
    elif currency == "sgd":
        amount = models.ExchangeRate.get_rate('gbp', 'sgd') * amount
        return render(request, "billing/top_up_bank_sgd.html", {
            "amount": amount,
            "ref": ref
        })
    elif currency == "ron":
        amount = models.ExchangeRate.get_rate('gbp', 'ron') * amount
        return render(request, "billing/top_up_bank_ron.html", {
            "amount": amount,
            "ref": ref
        })
    else:
        raise Http404()


@login_required
def top_up_bank_details(request, currency):
    account = request.user.account
    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    ref = secrets.token_hex(6).upper()

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by bank transfer",
        amount=amount,
        type=models.LedgerItem.TYPE_BACS,
        type_id=ref,
        state=models.LedgerItem.STATE_PENDING
    )
    ledger_item.save()
    currency = currency.lower()
    return show_bank_details(request, amount, ref, currency)


@login_required
def complete_top_up_bacs(request, item_id):
    return render(request, "billing/top_up_complete_currency_select.html", {
        "id": item_id
    })


@login_required
def complete_top_up_bank_details(request, item_id, currency):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden

    if ledger_item.state != ledger_item.STATE_PENDING:
        return redirect('dashboard')

    if ledger_item.type != ledger_item.TYPE_BACS:
        return HttpResponseBadRequest

    currency = currency.lower()
    return show_bank_details(request, ledger_item.amount, ledger_item.type_id, currency)


@login_required
def top_up_new_ach(request):
    session_id = secrets.token_hex(16)
    request.session["gc_session_id"] = session_id

    prefilled_bank_account = {}
    if "dd_account_type" in request.session:
        prefilled_bank_account["account_type"] = request.session.pop("dd_account_type")

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Top up",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('top_up_new_ach_complete')),
        "prefilled_customer": {
            "given_name": request.user.first_name,
            "family_name": request.user.last_name,
            "email": request.user.email,
        },
        "prefilled_bank_account": prefilled_bank_account,
        "scheme": "ach"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def top_up_new_autogiro(request):
    session_id = secrets.token_hex(16)
    request.session["gc_session_id"] = session_id

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Top up",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('top_up_new_autogiro_complete')),
        "prefilled_customer": {
            "given_name": request.user.first_name,
            "family_name": request.user.last_name,
            "email": request.user.email,
        },
        "scheme": "autogiro"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def top_up_new_bacs(request):
    session_id = secrets.token_hex(16)
    request.session["gc_session_id"] = session_id

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Top up",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('top_up_new_bacs_complete')),
        "prefilled_customer": {
            "given_name": request.user.first_name,
            "family_name": request.user.last_name,
            "email": request.user.email,
        },
        "scheme": "bacs"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def top_up_new_becs(request):
    session_id = secrets.token_hex(16)
    request.session["gc_session_id"] = session_id

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Top up",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('top_up_new_becs_complete')),
        "prefilled_customer": {
            "given_name": request.user.first_name,
            "family_name": request.user.last_name,
            "email": request.user.email,
        },
        "scheme": "becs"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def top_up_new_becs_nz(request):
    session_id = secrets.token_hex(16)
    request.session["gc_session_id"] = session_id

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Top up",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('top_up_new_becs_nz_complete')),
        "prefilled_customer": {
            "given_name": request.user.first_name,
            "family_name": request.user.last_name,
            "email": request.user.email,
        },
        "scheme": "becs_nz"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def top_up_new_betalingsservice(request):
    session_id = secrets.token_hex(16)
    request.session["gc_session_id"] = session_id

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Top up",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('top_up_new_betalingsservice_complete')),
        "prefilled_customer": {
            "given_name": request.user.first_name,
            "family_name": request.user.last_name,
            "email": request.user.email,
        },
        "scheme": "betalingsservice"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def top_up_new_pad(request):
    session_id = secrets.token_hex(16)
    request.session["gc_session_id"] = session_id

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Top up",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('top_up_new_pad_complete')),
        "prefilled_customer": {
            "given_name": request.user.first_name,
            "family_name": request.user.last_name,
            "email": request.user.email,
        },
        "scheme": "pad"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def top_up_new_sepa(request):
    session_id = secrets.token_hex(16)
    request.session["gc_session_id"] = session_id

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Top up",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('top_up_new_sepa_complete')),
        "prefilled_customer": {
            "given_name": request.user.first_name,
            "family_name": request.user.last_name,
            "email": request.user.email,
        },
        "scheme": "sepa_core"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def top_up_new_ach_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_session_id")
        }
    )

    models.ACHMandate.sync_mandate(redirect_flow.links.mandate, account)

    if "amount" not in request.session:
        return redirect("account_details")

    amount = decimal.Decimal(request.session.pop("amount"))
    amount_usd = models.ExchangeRate.get_rate('gbp', 'usd') * amount
    amount_int = int(amount_usd * decimal.Decimal(100))

    payment = gocardless_client.payments.create(params={
        "amount": amount_int,
        "currency": "USD",
        "description": "Top up",
        "retry_if_possible": False,
        "links": {
            "mandate": redirect_flow.links.mandate
        }
    })

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by ACH Direct Debit",
        amount=amount,
        type=models.LedgerItem.TYPE_GOCARDLESS,
        type_id=payment.id,
        state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
    )
    ledger_item.save()

    return redirect('dashboard')


@login_required
def top_up_new_autogiro_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_session_id")
        }
    )

    models.AutogiroMandate.sync_mandate(redirect_flow.links.mandate, account)

    if "amount" not in request.session:
        return redirect("account_details")

    amount = decimal.Decimal(request.session.pop("amount"))
    amount_sek = models.ExchangeRate.get_rate('gbp', 'sek') * amount
    amount_int = int(amount_sek * decimal.Decimal(100))

    payment = gocardless_client.payments.create(params={
        "amount": amount_int,
        "currency": "SEK",
        "description": "Top up",
        "retry_if_possible": False,
        "links": {
            "mandate": redirect_flow.links.mandate
        }
    })

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by Autogiro",
        amount=amount,
        type=models.LedgerItem.TYPE_GOCARDLESS,
        type_id=payment.id,
        state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
    )
    ledger_item.save()

    return redirect('dashboard')


@login_required
def top_up_new_bacs_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_session_id")
        }
    )

    models.GCBACSMandate.sync_mandate(redirect_flow.links.mandate, account)

    if "amount" not in request.session:
        return redirect("account_details")

    amount = decimal.Decimal(request.session.pop("amount"))
    amount_int = int(amount * decimal.Decimal(100))

    payment = gocardless_client.payments.create(params={
        "amount": amount_int,
        "currency": "GBP",
        "description": "Top up",
        "retry_if_possible": False,
        "links": {
            "mandate": redirect_flow.links.mandate
        }
    })

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by BACS Direct Debit",
        amount=amount,
        type=models.LedgerItem.TYPE_GOCARDLESS,
        type_id=payment.id,
        state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
    )
    ledger_item.save()

    return redirect('dashboard')


@login_required
def top_up_new_becs_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_session_id")
        }
    )

    models.BECSMandate.sync_mandate(redirect_flow.links.mandate, account)

    if "amount" not in request.session:
        return redirect("account_details")

    amount = decimal.Decimal(request.session.pop("amount"))
    amount_aud = models.ExchangeRate.get_rate('gbp', 'aud') * amount
    amount_int = int(amount_aud * decimal.Decimal(100))

    payment = gocardless_client.payments.create(params={
        "amount": amount_int,
        "currency": "AUD",
        "description": "Top up",
        "retry_if_possible": False,
        "links": {
            "mandate": redirect_flow.links.mandate
        }
    })

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by BECS Direct Debit",
        amount=amount,
        type=models.LedgerItem.TYPE_GOCARDLESS,
        type_id=payment.id,
        state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
    )
    ledger_item.save()

    return redirect('dashboard')


@login_required
def top_up_new_becs_nz_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_session_id")
        }
    )

    models.BECSNZMandate.sync_mandate(redirect_flow.links.mandate, account)

    if "amount" not in request.session:
        return redirect("account_details")

    amount = decimal.Decimal(request.session.pop("amount"))
    amount_nzd = models.ExchangeRate.get_rate('gbp', 'nzd') * amount
    amount_int = int(amount_nzd * decimal.Decimal(100))

    payment = gocardless_client.payments.create(params={
        "amount": amount_int,
        "currency": "NZD",
        "description": "Top up",
        "retry_if_possible": False,
        "links": {
            "mandate": redirect_flow.links.mandate
        }
    })

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by BECS NZ Direct Debit",
        amount=amount,
        type=models.LedgerItem.TYPE_GOCARDLESS,
        type_id=payment.id,
        state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
    )
    ledger_item.save()

    return redirect('dashboard')


@login_required
def top_up_new_betalingsservice_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_session_id")
        }
    )

    models.BetalingsserviceMandate.sync_mandate(redirect_flow.links.mandate, account)

    if "amount" not in request.session:
        return redirect("account_details")

    amount = decimal.Decimal(request.session.pop("amount"))
    amount_dkk = models.ExchangeRate.get_rate('gbp', 'dkk') * amount
    amount_int = int(amount_dkk * decimal.Decimal(100))

    payment = gocardless_client.payments.create(params={
        "amount": amount_int,
        "currency": "DKK",
        "description": "Top up",
        "retry_if_possible": False,
        "links": {
            "mandate": redirect_flow.links.mandate
        }
    })

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by Betalingsservice",
        amount=amount,
        type=models.LedgerItem.TYPE_GOCARDLESS,
        type_id=payment.id,
        state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
    )
    ledger_item.save()

    return redirect('dashboard')


@login_required
def top_up_new_pad_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_session_id")
        }
    )

    models.PADMandate.sync_mandate(redirect_flow.links.mandate, account)

    if "amount" not in request.session:
        return redirect("account_details")

    amount = decimal.Decimal(request.session.pop("amount"))
    amount_cad = models.ExchangeRate.get_rate('gbp', 'cad') * amount
    amount_int = int(amount_cad * decimal.Decimal(100))

    payment = gocardless_client.payments.create(params={
        "amount": amount_int,
        "currency": "CAD",
        "description": "Top up",
        "retry_if_possible": False,
        "links": {
            "mandate": redirect_flow.links.mandate
        }
    })

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by PAD Direct Debit",
        amount=amount,
        type=models.LedgerItem.TYPE_GOCARDLESS,
        type_id=payment.id,
        state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
    )
    ledger_item.save()

    return redirect('dashboard')


@login_required
def top_up_new_sepa_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_session_id")
        }
    )

    models.GCSEPAMandate.sync_mandate(redirect_flow.links.mandate, account)

    if "amount" not in request.session:
        return redirect("account_details")

    amount = decimal.Decimal(request.session.pop("amount"))
    amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
    amount_int = int(amount_eur * decimal.Decimal(100))

    payment = gocardless_client.payments.create(params={
        "amount": amount_int,
        "currency": "EUR",
        "description": "Top up",
        "retry_if_possible": False,
        "links": {
            "mandate": redirect_flow.links.mandate
        }
    })

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by SEPA Direct Debit",
        amount=amount,
        type=models.LedgerItem.TYPE_GOCARDLESS,
        type_id=payment.id,
        state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
    )
    ledger_item.save()

    return redirect('dashboard')


@login_required
def top_up_existing_bacs_direct_debit(request, mandate_id):
    account = request.user.account

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_int = int(amount * decimal.Decimal(100))

    gc_mandate = models.GCBACSMandate.objects.filter(id=mandate_id, active=True).first()

    if gc_mandate:
        if gc_mandate.account != request.user.account:
            return HttpResponseForbidden()

        payment = gocardless_client.payments.create(params={
            "amount": amount_int,
            "currency": "GBP",
            "description": "Top up",
            "retry_if_possible": False,
            "links": {
                "mandate": gc_mandate.mandate_id
            }
        })

        ledger_item = models.LedgerItem(
            account=account,
            descriptor="Top-up by BACS Direct Debit",
            amount=amount,
            type=models.LedgerItem.TYPE_GOCARDLESS,
            type_id=payment.id,
            state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
        )
        ledger_item.save()

        return redirect('dashboard')
    else:
        mandate = get_object_or_404(models.BACSMandate, id=mandate_id, active=True)

        if mandate.account != request.user.account:
            return HttpResponseForbidden()

        payment_intent = stripe.PaymentIntent.create(
            payment_method_types=['bacs_debit'],
            payment_method=mandate.payment_method,
            customer=account.get_stripe_id(),
            description='Top-up',
            confirm=True,
            amount=amount_int,
            receipt_email=request.user.email,
            return_url=request.build_absolute_uri(reverse('dashboard')),
            currency='gbp',
        )

        ledger_item = models.LedgerItem(
            account=account,
            descriptor="Top-up by BACS Direct Debit",
            amount=amount,
            type=models.LedgerItem.TYPE_CARD,
            type_id=payment_intent['id'],
            state=models.LedgerItem.STATE_PROCESSING
        )
        ledger_item.save()

        return redirect('dashboard')


@login_required
def top_up_existing_ach_direct_debit(request, mandate_id):
    account = request.user.account

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_usd = models.ExchangeRate.get_rate('gbp', 'usd') * amount
    amount_int = int(amount_usd * decimal.Decimal(100))

    mandate = get_object_or_404(models.BACSMandate, id=mandate_id, active=True)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    payment = gocardless_client.payments.create(params={
        "amount": amount_int,
        "currency": "USD",
        "description": "Top up",
        "retry_if_possible": False,
        "links": {
            "mandate": mandate.mandate_id
        }
    })

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by ACH Direct Debit",
        amount=amount,
        type=models.LedgerItem.TYPE_GOCARDLESS,
        type_id=payment.id,
        state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
    )
    ledger_item.save()

    return redirect('dashboard')


@login_required
def top_up_existing_autogiro_direct_debit(request, mandate_id):
    account = request.user.account

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_sek = models.ExchangeRate.get_rate('gbp', 'sek') * amount
    amount_int = int(amount_sek * decimal.Decimal(100))

    mandate = get_object_or_404(models.AutogiroMandate, id=mandate_id, active=True)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    payment = gocardless_client.payments.create(params={
        "amount": amount_int,
        "currency": "SEK",
        "description": "Top up",
        "retry_if_possible": False,
        "links": {
            "mandate": mandate.mandate_id
        }
    })

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by Autogiro",
        amount=amount,
        type=models.LedgerItem.TYPE_GOCARDLESS,
        type_id=payment.id,
        state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
    )
    ledger_item.save()

    return redirect('dashboard')


@login_required
def top_up_existing_becs_direct_debit(request, mandate_id):
    account = request.user.account

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_aud = models.ExchangeRate.get_rate('gbp', 'aud') * amount
    amount_int = int(amount_aud * decimal.Decimal(100))

    mandate = get_object_or_404(models.BECSMandate, id=mandate_id, active=True)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    payment = gocardless_client.payments.create(params={
        "amount": amount_int,
        "currency": "AUD",
        "description": "Top up",
        "retry_if_possible": False,
        "links": {
            "mandate": mandate.mandate_id
        }
    })

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by BECS Direct Debit",
        amount=amount,
        type=models.LedgerItem.TYPE_GOCARDLESS,
        type_id=payment.id,
        state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
    )
    ledger_item.save()

    return redirect('dashboard')


@login_required
def top_up_existing_becs_nz_direct_debit(request, mandate_id):
    account = request.user.account

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_nzd = models.ExchangeRate.get_rate('gbp', 'nzd') * amount
    amount_int = int(amount_nzd * decimal.Decimal(100))

    mandate = get_object_or_404(models.BECSNZMandate, id=mandate_id, active=True)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    payment = gocardless_client.payments.create(params={
        "amount": amount_int,
        "currency": "NZD",
        "description": "Top up",
        "retry_if_possible": False,
        "links": {
            "mandate": mandate.mandate_id
        }
    })

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by BECS NZ Direct Debit",
        amount=amount,
        type=models.LedgerItem.TYPE_GOCARDLESS,
        type_id=payment.id,
        state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
    )
    ledger_item.save()

    return redirect('dashboard')


@login_required
def top_up_existing_betalingsservice_direct_debit(request, mandate_id):
    account = request.user.account

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_dkk = models.ExchangeRate.get_rate('gbp', 'dkk') * amount
    amount_int = int(amount_dkk * decimal.Decimal(100))

    mandate = get_object_or_404(models.BetalingsserviceMandate, id=mandate_id, active=True)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    payment = gocardless_client.payments.create(params={
        "amount": amount_int,
        "currency": "DKK",
        "description": "Top up",
        "retry_if_possible": False,
        "links": {
            "mandate": mandate.mandate_id
        }
    })

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by Betalingsservice",
        amount=amount,
        type=models.LedgerItem.TYPE_GOCARDLESS,
        type_id=payment.id,
        state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
    )
    ledger_item.save()

    return redirect('dashboard')


@login_required
def top_up_existing_pad_direct_debit(request, mandate_id):
    account = request.user.account

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_cad = models.ExchangeRate.get_rate('gbp', 'cad') * amount
    amount_int = int(amount_cad * decimal.Decimal(100))

    mandate = get_object_or_404(models.PADMandate, id=mandate_id, active=True)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    payment = gocardless_client.payments.create(params={
        "amount": amount_int,
        "currency": "CAD",
        "description": "Top up",
        "retry_if_possible": False,
        "links": {
            "mandate": mandate.mandate_id
        }
    })

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by PAD Direct Debit",
        amount=amount,
        type=models.LedgerItem.TYPE_GOCARDLESS,
        type_id=payment.id,
        state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
    )
    ledger_item.save()

    return redirect('dashboard')


@login_required
def complete_top_up_checkout(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden

    if ledger_item.state != ledger_item.STATE_PENDING:
        return redirect('dashboard')

    if ledger_item.type != ledger_item.TYPE_CHECKOUT:
        return HttpResponseBadRequest

    return render(request, "billing/top_up_bacs_direct_debit.html", {
        "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
        "checkout_id": ledger_item.type_id,
        "is_new": True
    })


@login_required
def top_up_existing_sepa_direct_debit(request, mandate_id):
    account = request.user.account  # type: models.Account

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
    amount_int = int(amount_eur * decimal.Decimal(100))

    gc_mandate = models.GCSEPAMandate.objects.filter(id=mandate_id, active=True).first()

    if gc_mandate:
        if gc_mandate.account != request.user.account:
            return HttpResponseForbidden()

        payment = gocardless_client.payments.create(params={
            "amount": amount_int,
            "currency": "EUR",
            "description": "Top up",
            "retry_if_possible": False,
            "links": {
                "mandate": gc_mandate.mandate_id
            }
        })

        ledger_item = models.LedgerItem(
            account=account,
            descriptor="Top-up by SEPA Direct Debit",
            amount=amount,
            type=models.LedgerItem.TYPE_GOCARDLESS,
            type_id=payment.id,
            state=models.LedgerItem.STATE_PROCESSING_CANCELLABLE,
        )
        ledger_item.save()

        return redirect('dashboard')
    else:
        mandate = get_object_or_404(models.SEPAMandate, id=mandate_id, active=True)

        if mandate.account != request.user.account:
            return HttpResponseForbidden()

        payment_intent = stripe.PaymentIntent.create(
            payment_method_types=['sepa_debit'],
            payment_method=mandate.payment_method,
            customer=account.get_stripe_id(),
            description='Top-up',
            confirm=True,
            amount=amount_int,
            receipt_email=request.user.email,
            currency='eur',
        )

        ledger_item = models.LedgerItem(
            account=account,
            descriptor="Top-up by SEPA Direct Debit",
            amount=amount,
            type=models.LedgerItem.TYPE_SEPA,
            type_id=payment_intent['id'],
            state=models.LedgerItem.STATE_PROCESSING
        )
        ledger_item.save()

        return redirect('dashboard')


@login_required
def complete_top_up_sepa_direct_debit(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden

    if ledger_item.state != ledger_item.STATE_PENDING:
        return redirect('dashboard')

    if ledger_item.type != ledger_item.TYPE_SEPA:
        return HttpResponseBadRequest

    payment_intent = stripe.PaymentIntent.retrieve(ledger_item.type_id)

    if payment_intent["status"] == "succeeded":
        ledger_item.state = ledger_item.STATE_COMPLETED
        ledger_item.save()
        return redirect('dashboard')

    return render(request, "billing/top_up_sepa_direct_debit.html", {
        "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
        "client_secret": payment_intent["client_secret"],
        "is_new": True
    })


@login_required
def top_up_sofort(request):
    account = request.user.account

    if "charge_state_id" in request.session:
        charge_state = get_object_or_404(models.ChargeState, id=request.session["charge_state_id"])
    else:
        charge_state = None

    if request.method == "POST":
        form = forms.SOFORTForm(request.POST)
        if form.is_valid():
            if "amount" not in request.session:
                return redirect("top_up")
            amount = decimal.Decimal(request.session.pop("amount"))

            redirect_uri = reverse('complete_charge', args=(charge_state.id,)) if charge_state else reverse('dashboard')

            amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
            amount_int = int(amount_eur * decimal.Decimal(100))
            payment_intent = stripe.PaymentIntent.create(
                confirm=True,
                amount=amount_int,
                currency="eur",
                payment_method_types=["sofort"],
                payment_method_data={
                    "type": "sofort",
                    "billing_details": {
                        "email": request.user.email,
                        "name": f"{request.user.first_name} {request.user.last_name}"
                    },
                    "sofort": {
                        "country": form.cleaned_data['account_country'],
                    }
                },
                return_url=request.build_absolute_uri(redirect_uri),
                customer=account.get_stripe_id(),
                description='Top-up',
                setup_future_usage="off_session",
                mandate_data={
                    "customer_acceptance": {
                        "type": "online",
                        "online": {
                            "ip_address": str(get_ip(request)),
                            "user_agent": request.META["HTTP_USER_AGENT"]
                        }
                    }
                }
            )

            ledger_item = models.LedgerItem(
                account=account,
                descriptor="Top-up by SOFORT",
                amount=amount,
                type=models.LedgerItem.TYPE_SOFORT,
                type_id=payment_intent['id']
            )
            ledger_item.save()

            if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
                return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])

            return redirect(redirect_uri)

    return render(request, "billing/top_up_sofort.html")


@login_required
def top_up_giropay(request):
    account = request.user.account

    if "charge_state_id" in request.session:
        charge_state = get_object_or_404(models.ChargeState, id=request.session.pop("charge_state_id"))
    else:
        charge_state = None

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
    amount_int = int(amount_eur * decimal.Decimal(100))

    redirect_uri = reverse('complete_charge', args=(charge_state.id,)) if charge_state else reverse('dashboard')

    payment_intent = stripe.PaymentIntent.create(
        confirm=True,
        amount=amount_int,
        currency="eur",
        payment_method_types=["giropay"],
        payment_method_data={
            "type": "giropay",
            "billing_details": {
                "email": request.user.email,
                "name": f"{request.user.first_name} {request.user.last_name}"
            },
        },
        return_url=request.build_absolute_uri(redirect_uri),
        customer=account.get_stripe_id(),
        description='Top-up',
    )

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by GIROPAY",
        amount=amount,
        type=models.LedgerItem.TYPE_GIROPAY,
        type_id=payment_intent['id']
    )
    ledger_item.save()

    if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
        return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])

    return redirect(redirect_uri)


@login_required
def top_up_bancontact(request):
    account = request.user.account

    if "charge_state_id" in request.session:
        charge_state = get_object_or_404(models.ChargeState, id=request.session.pop("charge_state_id"))
    else:
        charge_state = None

    if request.method == "POST" and request.POST.get("accept") == "true":
        if "amount" not in request.session:
            return redirect("top_up")
        amount = decimal.Decimal(request.session.pop("amount"))
        amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
        amount_int = int(amount_eur * decimal.Decimal(100))

        redirect_uri = reverse('complete_charge', args=(charge_state.id,)) if charge_state else reverse('dashboard')

        payment_intent = stripe.PaymentIntent.create(
            confirm=True,
            amount=amount_int,
            currency="eur",
            payment_method_types=["bancontact"],
            payment_method_data={
                "type": "bancontact",
                "billing_details": {
                    "email": request.user.email,
                    "name": f"{request.user.first_name} {request.user.last_name}"
                },
            },
            return_url=request.build_absolute_uri(redirect_uri),
            customer=account.get_stripe_id(),
            description='Top-up',
            setup_future_usage="off_session",
            mandate_data={
                "customer_acceptance": {
                    "type": "online",
                    "online": {
                        "ip_address": str(get_ip(request)),
                        "user_agent": request.META["HTTP_USER_AGENT"]
                    }
                }
            }
        )

        ledger_item = models.LedgerItem(
            account=account,
            descriptor="Top-up by Bancontact",
            amount=amount,
            type=models.LedgerItem.TYPE_BANCONTACT,
            type_id=payment_intent['id']
        )
        ledger_item.save()

        if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
            return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])

        return redirect(redirect_uri)

    return render(request, "billing/top_up_mandate.html", {
        "scheme": "Bancontact"
    })


@login_required
def top_up_eps(request):
    account = request.user.account

    if "charge_state_id" in request.session:
        charge_state = get_object_or_404(models.ChargeState, id=request.session.pop("charge_state_id"))
    else:
        charge_state = None

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
    amount_int = int(amount_eur * decimal.Decimal(100))

    redirect_uri = reverse('complete_charge', args=(charge_state.id,)) if charge_state else reverse('dashboard')

    payment_intent = stripe.PaymentIntent.create(
        confirm=True,
        amount=amount_int,
        currency="eur",
        payment_method_types=["eps"],
        payment_method_data={
            "type": "eps",
            "billing_details": {
                "email": request.user.email,
                "name": f"{request.user.first_name} {request.user.last_name}"
            },
        },
        return_url=request.build_absolute_uri(redirect_uri),
        customer=account.get_stripe_id(),
        description='Top-up',
    )

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by EPS",
        amount=amount,
        type=models.LedgerItem.TYPE_EPS,
        type_id=payment_intent['id']
    )
    ledger_item.save()

    if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
        return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])

    return redirect(redirect_uri)


@login_required
def top_up_ideal(request):
    account = request.user.account

    if "charge_state_id" in request.session:
        charge_state = get_object_or_404(models.ChargeState, id=request.session.pop("charge_state_id"))
    else:
        charge_state = None

    if request.method == "POST" and request.POST.get("accept") == "true":
        if "amount" not in request.session:
            return redirect("top_up")
        amount = decimal.Decimal(request.session.pop("amount"))
        amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
        amount_int = int(amount_eur * decimal.Decimal(100))

        redirect_uri = reverse('complete_charge', args=(charge_state.id,)) if charge_state else reverse('dashboard')

        payment_intent = stripe.PaymentIntent.create(
            confirm=True,
            amount=amount_int,
            currency="eur",
            payment_method_types=["ideal"],
            payment_method_data={
                "type": "ideal",
                "billing_details": {
                    "email": request.user.email,
                    "name": f"{request.user.first_name} {request.user.last_name}"
                },
            },
            return_url=request.build_absolute_uri(redirect_uri),
            customer=account.get_stripe_id(),
            description='Top-up',
            setup_future_usage="off_session",
            mandate_data={
                "customer_acceptance": {
                    "type": "online",
                    "online": {
                        "ip_address": str(get_ip(request)),
                        "user_agent": request.META["HTTP_USER_AGENT"]
                    }
                }
            }
        )

        ledger_item = models.LedgerItem(
            account=account,
            descriptor="Top-up by iDEAL",
            amount=amount,
            type=models.LedgerItem.TYPE_IDEAL,
            type_id=payment_intent['id']
        )
        ledger_item.save()

        if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
            return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])

        return redirect(redirect_uri)

    return render(request, "billing/top_up_mandate.html", {
        "scheme": "iDEAL"
    })


@login_required
def top_up_multibanco(request):
    account = request.user.account

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
    amount_int = int(amount_eur * decimal.Decimal(100))
    source = stripe.Source.create(
        type='multibanco',
        amount=amount_int,
        currency='eur',
        owner={
            "email": request.user.email,
            "name": f"{request.user.first_name} {request.user.last_name}"
        },
        redirect={
            "return_url": request.build_absolute_uri(reverse('dashboard')),
        },
        statement_descriptor="AS207960 Top-up"
    )

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by Multibanco",
        amount=amount,
        type=models.LedgerItem.TYPE_SOURCES,
        type_id=source['id']
    )
    ledger_item.save()

    return redirect(source["redirect"]["url"])


@login_required
def top_up_p24(request):
    account = request.user.account

    if "charge_state_id" in request.session:
        charge_state = get_object_or_404(models.ChargeState, id=request.session.pop("charge_state_id"))
    else:
        charge_state = None

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
    amount_int = int(amount_eur * decimal.Decimal(100))

    redirect_uri = reverse('complete_charge', args=(charge_state.id,)) if charge_state else reverse('dashboard')

    payment_intent = stripe.PaymentIntent.create(
        confirm=True,
        amount=amount_int,
        currency="eur",
        payment_method_types=["p24"],
        payment_method_data={
            "type": "p24",
            "billing_details": {
                "email": request.user.email,
                "name": f"{request.user.first_name} {request.user.last_name}"
            },
        },
        return_url=request.build_absolute_uri(redirect_uri),
        customer=account.get_stripe_id(),
        description='Top-up',
    )

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by Przelewy24",
        amount=amount,
        type=models.LedgerItem.TYPE_P24,
        type_id=payment_intent['id']
    )
    ledger_item.save()

    if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
        return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])

    return redirect(redirect_uri)


@login_required
def complete_top_up_sources(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden

    if ledger_item.state != ledger_item.STATE_PENDING:
        return redirect('dashboard')

    if ledger_item.type != ledger_item.TYPE_SOURCES:
        return HttpResponseBadRequest

    source = stripe.Source.retrieve(ledger_item.type_id)

    if source["status"] != "pending":
        return redirect('dashboard')

    return redirect(source["redirect"]["url"])


@login_required
def fail_top_up(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden

    if ledger_item.state not in (ledger_item.STATE_PENDING, ledger_item.STATE_PROCESSING_CANCELLABLE):
        return redirect('dashboard')

    if ledger_item.type not in (
            ledger_item.TYPE_CARD, ledger_item.TYPE_BACS, ledger_item.TYPE_SOURCES, ledger_item.TYPE_CHECKOUT,
            ledger_item.TYPE_SEPA, ledger_item.TYPE_SOFORT, ledger_item.TYPE_GIROPAY, ledger_item.TYPE_BANCONTACT,
            ledger_item.TYPE_EPS, ledger_item.TYPE_IDEAL, ledger_item.TYPE_P24, ledger_item.TYPE_GOCARDLESS
    ):
        return HttpResponseBadRequest()

    if ledger_item.type in (
            ledger_item.TYPE_CARD, ledger_item.TYPE_SEPA, ledger_item.TYPE_SOFORT, ledger_item.TYPE_GIROPAY,
            ledger_item.TYPE_BANCONTACT, ledger_item.TYPE_EPS, ledger_item.TYPE_IDEAL, ledger_item.TYPE_P24
    ):
        payment_intent = stripe.PaymentIntent.retrieve(ledger_item.type_id)
        if payment_intent["status"] == "succeeded":
            ledger_item.state = ledger_item.STATE_COMPLETED
            ledger_item.save()
            return redirect('dashboard')
        stripe.PaymentIntent.cancel(ledger_item.type_id)
    elif ledger_item.type == ledger_item.TYPE_CHECKOUT:
        session = stripe.checkout.Session.retrieve(ledger_item.type_id)
        stripe.PaymentIntent.cancel(session["payment_intent"])
    elif ledger_item.type == ledger_item.TYPE_GOCARDLESS:
        gocardless_client.payments.cancel(ledger_item.type_id)

    ledger_item.state = models.LedgerItem.STATE_FAILED
    ledger_item.save()

    return redirect('dashboard')


@login_required
def fail_charge(request, charge_id):
    charge_state = get_object_or_404(models.ChargeState, id=charge_id)

    if charge_state.account != request.user.account:
        return HttpResponseForbidden

    if charge_state.ledger_item and charge_state.ledger_item.state != models.LedgerItem.STATE_COMPLETED:
        charge_state.ledger_item.state = models.LedgerItem.STATE_FAILED
        charge_state.ledger_item.save()

    return redirect('dashboard')


@login_required
def complete_charge(request, charge_id):
    charge_state = get_object_or_404(models.ChargeState, id=charge_id)

    with transaction.atomic():
        if not charge_state.account:
            charge_state.account = request.user.account
            charge_state.save()
        if charge_state.ledger_item and not charge_state.ledger_item.account:
            charge_state.ledger_item.account = request.user.account
            charge_state.ledger_item.save()
        if charge_state.payment_ledger_item and not charge_state.payment_ledger_item.account:
            charge_state.payment_ledger_item.account = request.user.account
            charge_state.payment_ledger_item.save()

    if charge_state.account != request.user.account:
        return HttpResponseForbidden()

    if request.method == "POST":
        if request.POST.get("action") == "cancel":
            if charge_state.ledger_item and charge_state.ledger_item.state != models.LedgerItem.STATE_COMPLETED:
                charge_state.ledger_item.state = models.LedgerItem.STATE_FAILED
                charge_state.ledger_item.save()

            return redirect(charge_state.full_redirect_uri())

        form = forms.CompleteChargeForm(request.POST)
        if form.is_valid():
            request.session["charge_state_id"] = str(charge_state.id)
            request.session["amount"] = str((charge_state.account.balance + charge_state.ledger_item.amount) * -1)
            if form.cleaned_data['method'] == forms.TopUpForm.METHOD_CARD:
                return redirect("top_up_card")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_GIROPAY:
                return redirect("top_up_giropay")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_BANCONTACT:
                return redirect("top_up_bancontact")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_EPS:
                return redirect("top_up_eps")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_IDEAL:
                return redirect("top_up_ideal")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_P24:
                return redirect("top_up_p24")
    else:
        if charge_state.ledger_item and charge_state.account.balance >= (-charge_state.ledger_item.amount):
            charge_state.ledger_item.state = charge_state.ledger_item.STATE_COMPLETED
            charge_state.ledger_item.save()

            return redirect(charge_state.full_redirect_uri())

        payment_intent = stripe.PaymentIntent.retrieve(charge_state.payment_ledger_item.type_id) \
            if (charge_state.payment_ledger_item and
                charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_CARD) \
            else None

        has_error = False
        if payment_intent:
            if payment_intent.get("last_payment_error"):
                charge_state.last_error = payment_intent["last_payment_error"]["message"] \
                    if payment_intent["last_payment_error"]["type"] == "card_error" else "Payment failed"
                charge_state.save()
                has_error = True
            else:
                if payment_intent["status"] == "requires_action":
                    if payment_intent["next_action"]["type"] == "use_stripe_sdk":
                        charge_state.payment_ledger_item.state = charge_state.payment_ledger_item.STATE_FAILED
                        charge_state.payment_ledger_item.save()
                        charge_state.last_error = "Card requires authentication"
                        charge_state.save()
                        has_error = True
                    elif payment_intent["next_action"]["type"] == "redirect_to_url":
                        return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])
                # if payment_intent["status"] != "succeeded":
                #     try:
                #         payment_intent.confirm()
                #     except (stripe.error.CardError, stripe.error.InvalidRequestError) as e:
                #         if isinstance(e, stripe.error.InvalidRequestError):
                #             message = "Payment failed"
                #         else:
                #             message = e["error"]["message"]
                #         charge_state.last_error = message
                #         charge_state.save()

        if charge_state.ledger_item:
            if charge_state.ledger_item.state in (
                    models.LedgerItem.STATE_FAILED
            ):
                return redirect(charge_state.full_redirect_uri())

        if charge_state.payment_ledger_item:
            if charge_state.payment_ledger_item.type in (
                    models.LedgerItem.TYPE_CARD, models.LedgerItem.TYPE_SEPA, models.LedgerItem.TYPE_SOFORT,
                    models.LedgerItem.TYPE_GIROPAY, models.LedgerItem.TYPE_BANCONTACT, models.LedgerItem.TYPE_EPS,
                    models.LedgerItem.TYPE_IDEAL, models.LedgerItem.TYPE_P24
            ):
                payment_intent = stripe.PaymentIntent.retrieve(charge_state.payment_ledger_item.type_id)
                update_from_payment_intent(payment_intent, charge_state.payment_ledger_item)
            elif charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_SOURCES:
                source = stripe.Source.retrieve(charge_state.payment_ledger_item.type_id)
                update_from_source(source, charge_state.payment_ledger_item)
            elif charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_CHARGES:
                charge = stripe.Charge.retrieve(charge_state.payment_ledger_item.type_id)
                update_from_charge(charge, charge_state.payment_ledger_item)
            elif charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_CHECKOUT:
                session = stripe.checkout.Session.retrieve(charge_state.payment_ledger_item.type_id)
                update_from_checkout_session(session, charge_state.payment_ledger_item)

            if charge_state.payment_ledger_item.state in (
                    models.LedgerItem.STATE_COMPLETED, models.LedgerItem.STATE_PROCESSING,
                    models.LedgerItem.STATE_PROCESSING_CANCELLABLE
            ):
                if charge_state.payment_ledger_item.state == models.LedgerItem.STATE_PROCESSING_CANCELLABLE:
                    charge_state.payment_ledger_item.state = models.LedgerItem.STATE_PROCESSING
                    charge_state.payment_ledger_item.save()
                if charge_state.ledger_item:
                    charge_state.ledger_item.state = charge_state.ledger_item.STATE_COMPLETED
                    charge_state.ledger_item.save()

                return redirect(charge_state.full_redirect_uri())
            elif charge_state.ledger_item and not has_error:
                charge_state.last_error = "Payment failed."
                charge_state.save()
        elif charge_state.ledger_item and not has_error:
            charge_state.last_error = "Insufficient funds in your account."
            charge_state.save()

        form = forms.CompleteChargeForm()

    return render(request, "billing/complete_charge.html", {
        "charge": charge_state,
        "form": form
    })


@login_required
def account_details(request):
    account = request.user.account  # type: models.Account
    cards = []

    if account.stripe_customer_id:
        cards = stripe.PaymentMethod.list(
            customer=account.stripe_customer_id,
            type="card"
        ).auto_paging_iter()

    def map_sepa_mandate(m):
        mandate = stripe.Mandate.retrieve(m.mandate_id)
        payment_method = stripe.PaymentMethod.retrieve(mandate["payment_method"])
        return {
            "id": m.id,
            "cc": payment_method["sepa_debit"]["country"],
            "last4": payment_method["sepa_debit"]["last4"],
            "bank": payment_method["sepa_debit"]["bank_code"],
            "ref": mandate["payment_method_details"]["sepa_debit"]["reference"],
            "url": mandate["payment_method_details"]["sepa_debit"]["url"],
            "status": "active" if m.active else "revoked",
            "active": m.active,
            "is_default": mandate["payment_method"] == account.default_stripe_payment_method_id,
        }

    def map_bacs_mandate(m):
        mandate = stripe.Mandate.retrieve(m.mandate_id)
        payment_method = stripe.PaymentMethod.retrieve(mandate["payment_method"])
        return {
            "id": m.id,
            "last4": payment_method["bacs_debit"]["last4"],
            "bank": payment_method["bacs_debit"]["sort_code"],
            "ref": mandate["payment_method_details"]["bacs_debit"]["reference"],
            "url": mandate["payment_method_details"]["bacs_debit"]["url"],
            "status": mandate["payment_method_details"]["bacs_debit"]["network_status"],
            "active": m.active,
            "is_default": mandate["payment_method"] == account.default_stripe_payment_method_id,
        }

    def map_gc_mandate(m, v):
        mandate = gocardless_client.mandates.get(m.mandate_id)
        bank_account = gocardless_client.customer_bank_accounts.get(mandate.links.customer_bank_account)
        return {
            "id": m.id,
            "cc": bank_account.country_code,
            "account_type": bank_account.account_type,
            "last4": bank_account.account_number_ending,
            "bank": bank_account.bank_name,
            "ref": mandate.reference,
            "active": m.active,
            "status": "pending" if mandate.status in ("pending_customer_approval", "pending_submission", "submitted")
            else (
                "refused" if mandate.status == "failed" else "revoked" if mandate.status == "cancelled"
                else mandate.status
            ),
            "url": reverse(v, args=(m.id,))
        }

    ach_mandates = list(map(
        lambda m: map_gc_mandate(m, 'view_ach_mandate'),
        models.ACHMandate.objects.filter(account=account, active=True)
    ))
    autogiro_mandates = list(map(
        lambda m: map_gc_mandate(m, 'view_autogiro_mandate'),
        models.AutogiroMandate.objects.filter(account=account, active=True)
    ))
    bacs_mandates = list(map(map_bacs_mandate, models.BACSMandate.objects.filter(account=account, active=True)))
    bacs_mandates += list(map(
        lambda m: map_gc_mandate(m, 'view_bacs_mandate'),
        models.GCBACSMandate.objects.filter(account=account, active=True))
    )
    becs_mandates = list(map(
        lambda m: map_gc_mandate(m, 'view_becs_mandate'),
        models.BECSMandate.objects.filter(account=account, active=True)
    ))
    becs_nz_mandates = list(map(
        lambda m: map_gc_mandate(m, 'view_becs_nz_mandate')
        , models.BECSNZMandate.objects.filter(account=account, active=True)
    ))
    betalingsservice_mandates = list(map(
        lambda m: map_gc_mandate(m, 'view_betalingsservice_mandate'),
        models.BetalingsserviceMandate.objects.filter(account=account, active=True)
    ))
    pad_mandates = list(map(
        lambda m: map_gc_mandate(m, 'view_pad_mandate'),
        models.PADMandate.objects.filter(account=account, active=True)
    ))
    sepa_mandates = list(map(map_sepa_mandate, models.SEPAMandate.objects.filter(account=account, active=True)))
    sepa_mandates += list(map(
        lambda m: map_gc_mandate(m, 'view_sepa_mandate'),
        models.GCSEPAMandate.objects.filter(account=account, active=True)
    ))

    subscriptions = request.user.account.subscription_set.all()

    return render(request, "billing/account_details.html", {
        "account": account,
        "cards": cards,
        "ach_mandates": ach_mandates,
        "autogiro_mandates": autogiro_mandates,
        "bacs_mandates": bacs_mandates,
        "becs_mandates": becs_mandates,
        "becs_nz_mandates": becs_nz_mandates,
        "betalingsservice_mandates": betalingsservice_mandates,
        "pad_mandates": pad_mandates,
        "sepa_mandates": sepa_mandates,
        "subscriptions": subscriptions,
        "error": request.session.pop("error", None)
    })


@login_required
def add_card(request):
    intent = stripe.SetupIntent.create(
        customer=request.user.account.get_stripe_id(),
        usage="off_session",
        payment_method_options={
            "card": {
                "request_three_d_secure": "any"
            }
        }
    )
    return render(request, "billing/add_card.html", {
        "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
        "client_secret": intent.client_secret,
        "customer_name": f"{request.user.first_name} {request.user.last_name}",
    })


@login_required
def edit_card(request, pm_id):
    payment_method = stripe.PaymentMethod.retrieve(pm_id)

    if payment_method['customer'] != request.user.account.stripe_customer_id:
        return HttpResponseForbidden()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "delete":
            stripe.PaymentMethod.detach(pm_id)
            if request.user.account.default_stripe_payment_method_id == pm_id:
                request.user.account.default_stripe_payment_method_id = None
                request.user.account.save()
            return redirect('account_details')

        elif action == "default":
            request.user.account.default_stripe_payment_method_id = pm_id
            request.user.account.default_gc_mandate_id = None
            request.user.account.save()
            return redirect('account_details')
        else:
            form = forms.EditCardForm(request.POST)
            if form.is_valid():
                billing_details = {}

                if form.cleaned_data['name']:
                    billing_details["name"] = form.cleaned_data['name']
                if form.cleaned_data['phone']:
                    billing_details['phone'] = form.cleaned_data['phone']
                if form.cleaned_data['email']:
                    billing_details['email'] = form.cleaned_data['email']

                address = {}
                if form.cleaned_data['address_line1']:
                    address["line1"] = form.cleaned_data['address_line1']
                if form.cleaned_data['address_line2']:
                    address["line2"] = form.cleaned_data['address_line2']
                if form.cleaned_data['address_city']:
                    address["city"] = form.cleaned_data['address_city']
                if form.cleaned_data['address_state']:
                    address["state"] = form.cleaned_data['address_state']
                if form.cleaned_data['address_postal_code']:
                    address["postal_code"] = form.cleaned_data['address_postal_code']
                if form.cleaned_data['address_country']:
                    address["country"] = form.cleaned_data['address_country']

                billing_details["address"] = address

                stripe.PaymentMethod.modify(
                    pm_id,
                    billing_details=billing_details
                )
                return redirect('account_details')
    else:
        form = forms.EditCardForm(initial={
            "name": payment_method["billing_details"]["name"],
            "email": payment_method["billing_details"]["email"],
            "phone": payment_method["billing_details"]["phone"],
            "address_line1": payment_method["billing_details"]["address"]["line1"],
            "address_line2": payment_method["billing_details"]["address"]["line2"],
            "address_city": payment_method["billing_details"]["address"]["city"],
            "address_state": payment_method["billing_details"]["address"]["state"],
            "address_postal_code": payment_method["billing_details"]["address"]["postal_code"],
            "address_country": payment_method["billing_details"]["address"]["country"],
        })

    return render(request, "billing/edit_card.html", {
        "form": form
    })


@login_required
@require_POST
def edit_sepa_mandate(request, m_id):
    mandate = get_object_or_404(models.SEPAMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    action = request.POST.get("action")

    if action == "delete":
        stripe.PaymentMethod.detach(mandate.payment_method)
        mandate.delete()

    elif action == "default" and mandate.active:
        request.user.account.default_stripe_payment_method_id = mandate.payment_method
        request.user.account.default_gc_mandate_id = None
        request.user.account.save()

    return redirect('account_details')


@login_required
def view_ach_mandate(request, m_id):
    mandate = get_object_or_404(models.ACHMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    pdf = gocardless_client.mandate_pdfs.create(params={
        "links": {
            "mandate": mandate.mandate_id
        }
    })

    return redirect(pdf.url)


@login_required
@require_POST
def edit_ach_mandate(request, m_id):
    action = request.POST.get("action")

    mandate = get_object_or_404(models.ACHMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    if action == "delete":
        gocardless_client.mandates.cancel(mandate.mandate_id)
        mandate.active = False
        mandate.save()

    elif action == "default" and mandate.active:
        request.user.account.default_gc_mandate_id = mandate.mandate_id
        request.user.account.default_stripe_payment_method_id = None
        request.user.account.save()

    return redirect('account_details')


@login_required
def view_autogiro_mandate(request, m_id):
    mandate = get_object_or_404(models.AutogiroMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    pdf = gocardless_client.mandate_pdfs.create(params={
        "links": {
            "mandate": mandate.mandate_id
        }
    })

    return redirect(pdf.url)


@login_required
@require_POST
def edit_autogiro_mandate(request, m_id):
    action = request.POST.get("action")

    mandate = get_object_or_404(models.AutogiroMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    if action == "delete":
        gocardless_client.mandates.cancel(mandate.mandate_id)
        mandate.active = False
        mandate.save()

    elif action == "default" and mandate.active:
        request.user.account.default_gc_mandate_id = mandate.mandate_id
        request.user.account.default_stripe_payment_method_id = None
        request.user.account.save()

    return redirect('account_details')


@login_required
def view_bacs_mandate(request, m_id):
    mandate = get_object_or_404(models.GCBACSMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    pdf = gocardless_client.mandate_pdfs.create(params={
        "links": {
            "mandate": mandate.mandate_id
        }
    })

    return redirect(pdf.url)


@login_required
@require_POST
def edit_bacs_mandate(request, m_id):
    action = request.POST.get("action")

    gc_mandate = models.GCBACSMandate.objects.filter(id=m_id).first()
    if gc_mandate:
        if gc_mandate.account != request.user.account:
            return HttpResponseForbidden()

        if action == "delete":
            gocardless_client.mandates.cancel(gc_mandate.mandate_id)
            gc_mandate.active = False
            gc_mandate.save()

        elif action == "default" and gc_mandate.active:
            request.user.account.default_gc_mandate_id = gc_mandate.mandate_id
            request.user.account.default_stripe_payment_method_id = None
            request.user.account.save()
    else:
        mandate = get_object_or_404(models.BACSMandate, id=m_id)

        if mandate.account != request.user.account:
            return HttpResponseForbidden()

        if action == "delete":
            stripe.PaymentMethod.detach(mandate.payment_method)
            mandate.delete()

        elif action == "default" and mandate.active:
            request.user.account.default_stripe_payment_method_id = mandate.payment_method
            request.user.account.default_gc_mandate_id = None
            request.user.account.save()

    return redirect('account_details')


@login_required
def view_becs_mandate(request, m_id):
    mandate = get_object_or_404(models.BECSMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    pdf = gocardless_client.mandate_pdfs.create(params={
        "links": {
            "mandate": mandate.mandate_id
        }
    })

    return redirect(pdf.url)


@login_required
@require_POST
def edit_becs_mandate(request, m_id):
    action = request.POST.get("action")

    mandate = get_object_or_404(models.BECSMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    if action == "delete":
        gocardless_client.mandates.cancel(mandate.mandate_id)
        mandate.active = False
        mandate.save()

    elif action == "default" and mandate.active:
        request.user.account.default_gc_mandate_id = mandate.mandate_id
        request.user.account.default_stripe_payment_method_id = None
        request.user.account.save()

    return redirect('account_details')


@login_required
def view_becs_nz_mandate(request, m_id):
    mandate = get_object_or_404(models.BECSNZMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    pdf = gocardless_client.mandate_pdfs.create(params={
        "links": {
            "mandate": mandate.mandate_id
        }
    })

    return redirect(pdf.url)


@login_required
@require_POST
def edit_becs_nz_mandate(request, m_id):
    action = request.POST.get("action")

    mandate = get_object_or_404(models.BECSNZMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    if action == "delete":
        gocardless_client.mandates.cancel(mandate.mandate_id)
        mandate.active = False
        mandate.save()

    elif action == "default" and mandate.active:
        request.user.account.default_gc_mandate_id = mandate.mandate_id
        request.user.account.default_stripe_payment_method_id = None
        request.user.account.save()

    return redirect('account_details')


@login_required
def view_betalingsservice_mandate(request, m_id):
    mandate = get_object_or_404(models.BetalingsserviceMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    pdf = gocardless_client.mandate_pdfs.create(params={
        "links": {
            "mandate": mandate.mandate_id
        }
    })

    return redirect(pdf.url)


@login_required
@require_POST
def edit_betalingsservice_mandate(request, m_id):
    action = request.POST.get("action")

    mandate = get_object_or_404(models.BetalingsserviceMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    if action == "delete":
        gocardless_client.mandates.cancel(mandate.mandate_id)
        mandate.active = False
        mandate.save()

    elif action == "default" and mandate.active:
        request.user.account.default_gc_mandate_id = mandate.mandate_id
        request.user.account.default_stripe_payment_method_id = None
        request.user.account.save()

    return redirect('account_details')


@login_required
def view_pad_mandate(request, m_id):
    mandate = get_object_or_404(models.PADMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    pdf = gocardless_client.mandate_pdfs.create(params={
        "links": {
            "mandate": mandate.mandate_id
        }
    })

    return redirect(pdf.url)


@login_required
@require_POST
def edit_pad_mandate(request, m_id):
    action = request.POST.get("action")

    mandate = get_object_or_404(models.PADMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    if action == "delete":
        gocardless_client.mandates.cancel(mandate.mandate_id)
        mandate.active = False
        mandate.save()

    elif action == "default" and mandate.active:
        request.user.account.default_gc_mandate_id = mandate.mandate_id
        request.user.account.default_stripe_payment_method_id = None
        request.user.account.save()

    return redirect('account_details')


@login_required
def view_sepa_mandate(request, m_id):
    mandate = get_object_or_404(models.GCSEPAMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    pdf = gocardless_client.mandate_pdfs.create(params={
        "links": {
            "mandate": mandate.mandate_id
        }
    })

    return redirect(pdf.url)


@login_required
@require_POST
def edit_sepa_mandate(request, m_id):
    action = request.POST.get("action")

    gc_mandate = models.GCSEPAMandate.objects.filter(id=m_id).first()
    if gc_mandate:
        if gc_mandate.account != request.user.account:
            return HttpResponseForbidden()

        if action == "delete":
            gocardless_client.mandates.cancel(gc_mandate.mandate_id)
            gc_mandate.active = False
            gc_mandate.save()

        elif action == "default" and gc_mandate.active:
            request.user.account.default_gc_mandate_id = gc_mandate.mandate_id
            request.user.account.default_stripe_payment_method_id = None
            request.user.account.save()
    else:
        mandate = get_object_or_404(models.SEPAMandate, id=m_id)

        if mandate.account != request.user.account:
            return HttpResponseForbidden()

        if action == "delete":
            stripe.PaymentMethod.detach(mandate.payment_method)
            mandate.delete()

        elif action == "default" and mandate.active:
            request.user.account.default_stripe_payment_method_id = mandate.payment_method
            request.user.account.default_gc_mandate_id = None
            request.user.account.save()

    return redirect('account_details')


@login_required
def edit_subscription(request, s_id):
    subscription = get_object_or_404(models.Subscription, id=s_id)

    if subscription.account != request.user.account:
        return HttpResponseForbidden()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "retry":
            try:
                tasks.charge_account(
                    subscription.account, subscription.amount_unpaid, subscription.plan.name, f"sb_{subscription.id}",
                    off_session=False, return_uri=request.build_absolute_uri(request.get_full_path())
                )
            except tasks.ChargeError as e:
                request.session["error"] = e.message
                return redirect('account_details')
            except tasks.ChargeStateRequiresActionError as e:
                request.session["charge_state_id"] = str(e.charge_state.id)
                return redirect(e.redirect_url)

            subscription.state = subscription.STATE_ACTIVE
            subscription.last_billed = timezone.now()
            subscription.amount_unpaid = decimal.Decimal("0")
            subscription.save()
    else:
        if "charge_state_id" in request.GET:
            charge_state = get_object_or_404(models.ChargeState, id=request.GET.get("charge_state_id"))
            if not charge_state.is_complete():
                request.session["error"] = charge_state.last_error
                return redirect('account_details')

    return redirect('account_details')


@login_required
def statement_export(request):
    if request.method == "POST":
        form = forms.StatementExportForm(request.POST)
        if form.is_valid():
            from_date = form.cleaned_data["date_from"]
            to_date = form.cleaned_data["date_to"]
            items = models.LedgerItem.objects.filter(
                account=request.user.account,
                timestamp__gte=from_date,
                timestamp__lte=to_date,
                state=models.LedgerItem.STATE_COMPLETED
            )
            if form.cleaned_data["format"] == forms.StatementExportForm.FORMAT_CSV:
                response = HttpResponse(content_type='text/csv; charset=utf-8')
                response['Content-Disposition'] = \
                    f"attachment; filename=\"glauca-transactions-{from_date}-{to_date}.csv\""

                fieldnames = ["Transaction ID", "Date", "Time", "Description", "Amount", "Currency"]
                writer = csv.DictWriter(response, fieldnames=fieldnames)
                writer.writeheader()

                writer.writerows(map(lambda i: {
                    "Transaction ID": i.id,
                    "Date": i.timestamp.date(),
                    "Time": i.timestamp.time(),
                    "Description": i.descriptor,
                    "Amount": i.amount,
                    "Currency": "GBP"
                }, items))

                return response
            elif form.cleaned_data["format"] == forms.StatementExportForm.FORMAT_QIF:
                response = HttpResponse(content_type='application/qif ; charset=utf-8')
                response['Content-Disposition'] = \
                    f"attachment; filename=\"glauca-transactions-{from_date}-{to_date}.qif\""

                t = loader.get_template("billing/statement_export_qif.txt")
                response.write(t.render({
                    "account": request.user.account,
                    "items": items
                }))

                return response
            elif form.cleaned_data["format"] == forms.StatementExportForm.FORMAT_PDF:
                return render(request, "billing/statement_export_pdf.html", {
                    "account": request.user.account,
                    "items": items,
                    "from_date": from_date,
                    "to_date": to_date
                })
    else:
        form = forms.StatementExportForm()

    return render(request, "billing/statement_export.html", {
        "form": form
    })


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
            update_from_payment_intent(payment_intent)
        elif event.type in ('source.failed', 'source.chargeable', 'source.canceled'):
            source = event.data.object
            update_from_source(source)
        elif event.type in ('charge.pending', 'charge.succeeded', 'charge.failed', 'charge.succeeded',
                            'charge.refunded'):
            charge = event.data.object
            update_from_charge(charge)
        elif event.type in ("checkout.session.completed", "checkout.session.async_payment_failed",
                            "checkout.session.async_payment_succeeded"):
            session = event.data.object
            update_from_checkout_session(session)
        elif event.type == "setup_intent.succeeded":
            session = event.data.object
            setup_intent_succeeded(session)
        elif event.type == "mandate.updated":
            mandate = event.data.object
            mandate_update(mandate)
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
                ledger_item = models.LedgerItem.objects.filter(
                    type=models.LedgerItem.TYPE_GOCARDLESS, type_id=event["links"]["payment"]
                ).first()

                if not ledger_item:
                    continue

                if event["action"] == "submitted":
                    ledger_item.state = models.LedgerItem.STATE_PROCESSING
                    ledger_item.save()
                elif event["action"] == "confirmed":
                    ledger_item.state = models.LedgerItem.STATE_COMPLETED
                    ledger_item.save()
                elif event["action"] in ("failed", "cancelled"):
                    ledger_item.state = models.LedgerItem.STATE_FAILED
                    ledger_item.save()
            elif event["resource_type"] == "mandates":
                scheme = event["details"].get("scheme")
                if scheme == "ach":
                    models.ACHMandate.sync_mandate(event["links"]["mandate"], None)

    return HttpResponse(status=204)


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

        r = requests.get(
            f"{api_base}/v3/profiles/{profile_id}"
            f"/borderless-accounts/{account_id}/statement.json",
            headers={
                "Authorization": f"Bearer {settings.TRANSFERWISE_TOKEN}"
            },
            params={
                "currency": currency,
                "intervalStart": (credit_time - datetime.timedelta(seconds=5)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                "intervalEnd": (credit_time + datetime.timedelta(seconds=5)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                "type": "COMPACT"
            }
        )
        r.raise_for_status()
        data = r.json()
        transactions = data["transactions"]
        credit_transactions = filter(
            lambda t: t["type"] == "CREDIT" and t["details"]["type"] == "DEPOSIT",
            transactions
        )
        found_t = None
        for t in credit_transactions:
            if t["amount"]["value"] == amount and t["runningBalance"]["value"] == post_balance:
                found_t = t
                break

        if found_t:
            ref = found_t["details"].get("paymentReference")
            if ref:
                ledger_item = models.LedgerItem.objects.filter(
                    type=models.LedgerItem.TYPE_BACS,
                    type_id__contains=ref.upper(),
                    state=models.LedgerItem.STATE_PENDING
                ).first()

                if ledger_item:
                    ledger_item.amount = decimal.Decimal(found_t["amount"]["value"])
                    ledger_item.state = models.LedgerItem.STATE_COMPLETED
                    ledger_item.save()

    return HttpResponse(status=204)


def update_from_payment_intent(payment_intent, ledger_item=None):
    ledger_item = models.LedgerItem.objects.filter(
        Q(type=models.LedgerItem.TYPE_CARD) | Q(type=models.LedgerItem.TYPE_SEPA) |
        Q(type=models.LedgerItem.TYPE_SOFORT) | Q(type=models.LedgerItem.TYPE_GIROPAY) |
        Q(type=models.LedgerItem.TYPE_BANCONTACT) | Q(type=models.LedgerItem.TYPE_EPS) |
        Q(type=models.LedgerItem.TYPE_IDEAL) | Q(type=models.LedgerItem.TYPE_P24)
    ).filter(type_id=payment_intent['id']).first() if not ledger_item else ledger_item

    if not ledger_item:
        return

    for charge in payment_intent["charges"]["data"]:
        if charge["payment_method_details"]["type"] == "sepa_debit":
            models.SEPAMandate.sync_mandate(
                charge["payment_method_details"]["sepa_debit"]["mandate"],
                ledger_item.account if ledger_item else
                models.Account.objects.filter(stripe_customer_id=payment_intent["customer"]).first()
            )
        elif charge["payment_method_details"]["type"] == "sofort":
            if "generated_sepa_debit_mandate" in charge["payment_method_details"]["sofort"]:
                models.SEPAMandate.sync_mandate(
                    charge["payment_method_details"]["sofort"]["generated_sepa_debit_mandate"],
                    ledger_item.account if ledger_item else
                    models.Account.objects.filter(stripe_customer_id=payment_intent["customer"]).first()
                )
        elif charge["payment_method_details"]["type"] == "bancontact":
            if "generated_sepa_debit_mandate" in charge["payment_method_details"]["bancontact"]:
                models.SEPAMandate.sync_mandate(
                    charge["payment_method_details"]["bancontact"]["generated_sepa_debit_mandate"],
                    ledger_item.account if ledger_item else
                    models.Account.objects.filter(stripe_customer_id=payment_intent["customer"]).first()
                )
        elif charge["payment_method_details"]["type"] == "ideal":
            if "generated_sepa_debit_mandate" in charge["payment_method_details"]["ideal"]:
                models.SEPAMandate.sync_mandate(
                    charge["payment_method_details"]["ideal"]["generated_sepa_debit_mandate"],
                    ledger_item.account if ledger_item else
                    models.Account.objects.filter(stripe_customer_id=payment_intent["customer"]).first()
                )

    if payment_intent["status"] == "succeeded":
        amount = decimal.Decimal(payment_intent["amount_received"]) / decimal.Decimal(100)
        amount = models.ExchangeRate.get_rate(payment_intent['currency'], 'gbp') * amount
        ledger_item.amount = amount
        ledger_item.state = models.LedgerItem.STATE_COMPLETED
        ledger_item.save()
    elif payment_intent["status"] == "processing":
        ledger_item.state = models.LedgerItem.STATE_PROCESSING
        ledger_item.save()
    elif payment_intent["status"] == "requires_action":
        ledger_item.state = models.LedgerItem.STATE_PENDING
        ledger_item.save()
    elif (payment_intent["status"] == "requires_payment_method" and payment_intent["last_payment_error"]) \
            or payment_intent["status"] == "canceled":
        ledger_item.state = models.LedgerItem.STATE_FAILED
        ledger_item.save()


def update_from_source(source, ledger_item=None):
    ledger_item = models.LedgerItem.objects.filter(
        type=models.LedgerItem.TYPE_SOURCES,
        type_id=source['id']
    ).first() if not ledger_item else ledger_item

    if not ledger_item:
        return

    if source["status"] == "chargeable":
        charge = stripe.Charge.create(
            amount=source['amount'],
            currency=source['currency'],
            source=source['id'],
            customer=ledger_item.account.get_stripe_id(),
            receipt_email=ledger_item.account.user.email,
        )
        ledger_item.type_id = charge['id']
        ledger_item.type = models.LedgerItem.TYPE_CHARGES
        ledger_item.state = models.LedgerItem.STATE_PROCESSING
        ledger_item.save()
        update_from_charge(charge, ledger_item)
    elif source["status"] in ("failed", "canceled"):
        ledger_item.state = models.LedgerItem.STATE_FAILED
        ledger_item.save()


def update_from_charge(charge, ledger_item=None):
    if charge["payment_method_details"]["type"] == "sepa_debit":
        models.SEPAMandate.sync_mandate(
            charge["payment_method_details"]["sepa_debit"]["mandate"],
            models.Account.objects.filter(stripe_customer_id=charge["customer"]).first()
        )
    elif charge["payment_method_details"]["type"] == "sofort":
        if "generated_sepa_debit_mandate" in charge["payment_method_details"]["sofort"]:
            models.SEPAMandate.sync_mandate(
                charge["payment_method_details"]["sofort"]["generated_sepa_debit_mandate"],
                models.Account.objects.filter(stripe_customer_id=charge["customer"]).first()
            )
    elif charge["payment_method_details"]["type"] == "bancontact":
        if "generated_sepa_debit_mandate" in charge["payment_method_details"]["bancontact"]:
            models.SEPAMandate.sync_mandate(
                charge["payment_method_details"]["bancontact"]["generated_sepa_debit_mandate"],
                models.Account.objects.filter(stripe_customer_id=charge["customer"]).first()
            )
    elif charge["payment_method_details"]["type"] == "ideal":
        if "generated_sepa_debit_mandate" in charge["payment_method_details"]["ideal"]:
            models.SEPAMandate.sync_mandate(
                charge["payment_method_details"]["ideal"]["generated_sepa_debit_mandate"],
                models.Account.objects.filter(stripe_customer_id=charge["customer"]).first()
            )

    ledger_item = models.LedgerItem.objects.filter(
        type=models.LedgerItem.TYPE_CHARGES,
        type_id=charge['id']
    ).first() if not ledger_item else ledger_item

    if charge["refunded"]:
        if not ledger_item and charge['payment_intent']:
            ledger_item = models.LedgerItem.objects.filter(
                type=models.LedgerItem.TYPE_CARD,
                type_id=charge['payment_intent']
            ).first() if not ledger_item else ledger_item

        if ledger_item:
            reversal_ledger_item = models.LedgerItem.objects.filter(
                type=models.LedgerItem.TYPE_CHARGE,
                type_id=ledger_item.id,
                is_reversal=True
            ).first()  # type: models.LedgerItem
            if not reversal_ledger_item:
                new_ledger_item = models.LedgerItem(
                    account=ledger_item.account,
                    descriptor=ledger_item.descriptor,
                    amount=-(decimal.Decimal(charge["amount_refunded"]) / decimal.Decimal(100)),
                    type=models.LedgerItem.TYPE_CHARGE,
                    type_id=ledger_item.id,
                    timestamp=timezone.now(),
                    state=ledger_item.STATE_COMPLETED,
                    is_reversal=True
                )
                new_ledger_item.save()

    if not ledger_item:
        return

    if charge["status"] == "succeeded":
        ledger_item.state = models.LedgerItem.STATE_COMPLETED
        ledger_item.save()
    elif charge["status"] == "pending":
        ledger_item.state = models.LedgerItem.STATE_PROCESSING
        ledger_item.save()
    elif charge["status"] == "failed":
        ledger_item.state = models.LedgerItem.STATE_FAILED
        ledger_item.save()


def update_from_checkout_session(session, ledger_item=None):
    ledger_item = models.LedgerItem.objects.filter(
        type=models.LedgerItem.TYPE_CHECKOUT,
        type_id=session['id']
    ).first() if not ledger_item else ledger_item

    if ledger_item:
        ledger_item.state = models.LedgerItem.STATE_PROCESSING
        ledger_item.save()

    if session["mode"] == "payment":
        payment_intent = stripe.PaymentIntent.retrieve(session["payment_intent"])
        for charge in payment_intent["charges"]["data"]:
            account = ledger_item.account if ledger_item else models.Account.objects.filter(
                stripe_customer_id=payment_intent["customer"]
            ).first()
            if charge["payment_method_details"]["type"] == "bacs_debit":
                models.BACSMandate.sync_mandate(
                    charge["payment_method_details"]["bacs_debit"]["mandate"],
                    account
                )
            elif charge["payment_method_details"]["type"] == "sepa_debit":
                models.SEPAMandate.sync_mandate(
                    charge["payment_method_details"]["bacs_debit"]["mandate"],
                    account
                )
            elif charge["payment_method_details"]["type"] == "sofort":
                if "generated_sepa_debit_mandate" in charge["payment_method_details"]["sofort"]:
                    models.SEPAMandate.sync_mandate(
                        charge["payment_method_details"]["sofort"]["generated_sepa_debit_mandate"],
                        account
                    )
            elif charge["payment_method_details"]["type"] == "bancontact":
                if "generated_sepa_debit_mandate" in charge["payment_method_details"]["bancontact"]:
                    models.SEPAMandate.sync_mandate(
                        charge["payment_method_details"]["bancontact"]["generated_sepa_debit_mandate"],
                        account
                    )
            elif charge["payment_method_details"]["type"] == "ideal":
                if "generated_sepa_debit_mandate" in charge["payment_method_details"]["ideal"]:
                    models.SEPAMandate.sync_mandate(
                        charge["payment_method_details"]["ideal"]["generated_sepa_debit_mandate"],
                        account
                    )

        update_from_payment_intent(payment_intent, ledger_item)
    elif session["mode"] == "setup":
        setup_intent = stripe.SetupIntent.retrieve(session["setup_intent"])
        if "bacs_debit" in session["payment_method_types"]:
            models.BACSMandate.sync_mandate(
                setup_intent["mandate"], ledger_item.account if ledger_item else
                models.Account.objects.filter(stripe_customer_id=setup_intent["customer"]).first()
            )
        if "sepa_debit" in session["payment_method_types"]:
            models.SEPAMandate.sync_mandate(
                setup_intent["mandate"], ledger_item.account if ledger_item else
                models.Account.objects.filter(stripe_customer_id=setup_intent["customer"]).first()
            )


def setup_intent_succeeded(setup_intent):
    if "sepa_debit" in setup_intent["payment_method_types"]:
        models.SEPAMandate.sync_mandate(
            setup_intent["mandate"],
            models.Account.objects.filter(stripe_customer_id=setup_intent["customer"]).first()
        )


def mandate_update(mandate):
    payment_method = stripe.PaymentMethod.retrieve(mandate["payment_method"])
    account = models.Account.objects.filter(stripe_customer_id=payment_method["customer"]).first()
    if mandate["payment_method_details"]["type"] == "bacs_debit":
        models.BACSMandate.sync_mandate(
            mandate["id"], account
        )
    elif mandate["payment_method_details"]["type"] == "sepa_debit":
        models.BACSMandate.sync_mandate(
            mandate["id"], account
        )


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
        if ref:
            ledger_item = models.LedgerItem.objects.filter(
                type=models.LedgerItem.TYPE_BACS,
                type_id__contains=ref.upper(),
                state=models.LedgerItem.STATE_PENDING
            ).first()

            if ledger_item:
                ledger_item.amount = decimal.Decimal(data.get("amount")) / decimal.Decimal(100)
                ledger_item.state = models.LedgerItem.STATE_COMPLETED
                ledger_item.save()
    else:
        return HttpResponseBadRequest()

    return HttpResponse(status=200)


def check_api_auth(request):
    auth = request.META.get("HTTP_AUTHORIZATION")
    if not auth or not auth.startswith("Bearer "):
        return HttpResponseForbidden()

    try:
        claims = django_keycloak_auth.clients.verify_token(
            auth[len("Bearer "):].strip()
        )
    except keycloak.exceptions.KeycloakClientError:
        return HttpResponseForbidden()

    if "charge-user" not in claims.get("resource_access", {}).get(
            settings.OIDC_CLIENT_ID, {}
    ).get("roles", []):
        return HttpResponseForbidden()

    return None


@csrf_exempt
@require_POST
def convert_currency(request):
    auth_error = check_api_auth(request)
    if auth_error:
        return auth_error

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest()

    if "from" not in data or "to" not in data or "amount" not in data:
        return HttpResponseBadRequest()

    try:
        amount = decimal.Decimal(data["amount"])
    except decimal.InvalidOperation:
        return HttpResponseBadRequest()

    amount = models.ExchangeRate.get_rate(data["from"], data["to"]) * amount

    return HttpResponse(json.dumps({
        "amount": str(amount)
    }), content_type='application/json', status=200)


@csrf_exempt
@require_POST
@idempotency_key(optional=True)
def charge_user(request, user_id):
    auth_error = check_api_auth(request)
    if auth_error:
        return auth_error

    user = get_user_model().objects.filter(username=user_id).first()
    account = user.account if user else None  # type: models.Account

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest()

    if "amount" not in data or "descriptor" not in data or "id" not in data:
        return HttpResponseBadRequest()

    can_reject = data.get("can_reject", True)
    off_session = data.get("off_session", True)

    try:
        amount = decimal.Decimal(data["amount"]) / decimal.Decimal(100)
    except decimal.InvalidOperation:
        return HttpResponseBadRequest()

    with transaction.atomic():
        try:
            charge_state = tasks.charge_account(
                account, amount, data["descriptor"], data["id"], can_reject=can_reject, off_session=off_session,
                return_uri=data.get("return_uri")
            )
        except tasks.ChargeError as e:
            return HttpResponse(json.dumps({
                "message": e.message,
                "charge_state_id": str(e.charge_state.id)
            }), content_type='application/json', status=402)
        except tasks.ChargeStateRequiresActionError as e:
            return HttpResponse(json.dumps({
                "redirect_uri": e.redirect_url,
                "charge_state_id": str(e.charge_state.id)
            }), content_type='application/json', status=302)

        return HttpResponse(json.dumps({
            "charge_state_id": str(charge_state.id)
        }), content_type='application/json', status=200)


@csrf_exempt
def get_charge_state(request, charge_state_id):
    auth_error = check_api_auth(request)
    if auth_error:
        return auth_error

    charge_state = get_object_or_404(models.ChargeState, id=charge_state_id)

    if charge_state.ledger_item:
        status = charge_state.ledger_item.state
    elif charge_state.payment_ledger_item:
        status = charge_state.payment_ledger_item.state
    else:
        status = ""

    if status == models.LedgerItem.STATE_PENDING:
        status = "pending"
    elif status == models.LedgerItem.STATE_PROCESSING:
        status = "processing"
    elif status == models.LedgerItem.STATE_FAILED:
        status = "failed"
    elif status == models.LedgerItem.STATE_COMPLETED:
        status = "completed"
    else:
        status = "unknown"

    return HttpResponse(json.dumps({
        "status": status,
        "redirect_uri": settings.EXTERNAL_URL_BASE + reverse('complete_charge', args=(charge_state.id,)),
        "account": charge_state.account.user.username if charge_state.account else None,
        "last_error": charge_state.last_error
    }), content_type='application/json', status=200)


@csrf_exempt
@require_POST
@idempotency_key(optional=True)
def reverse_charge(request):
    auth_error = check_api_auth(request)
    if auth_error:
        return auth_error

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest()

    if "id" not in data:
        return HttpResponseBadRequest()

    with transaction.atomic():
        ledger_item = models.LedgerItem.objects.filter(
            type=models.LedgerItem.TYPE_CHARGE,
            type_id=data["id"],
            is_reversal=False,
            state=models.LedgerItem.STATE_COMPLETED
        ).first()
        if ledger_item:
            reversal_ledger_item = models.LedgerItem.objects.filter(
                type=models.LedgerItem.TYPE_CHARGE,
                type_id=data["id"],
                is_reversal=True,
                state=models.LedgerItem.STATE_COMPLETED
            ).first()  # type: models.LedgerItem
            if not (reversal_ledger_item and reversal_ledger_item.timestamp >= ledger_item.item):
                new_ledger_item = models.LedgerItem(
                    account=ledger_item.account,
                    descriptor=ledger_item.descriptor,
                    amount=-ledger_item.amount,
                    type=models.LedgerItem.TYPE_CHARGE,
                    type_id=ledger_item.type_id,
                    timestamp=timezone.now(),
                    state=ledger_item.STATE_COMPLETED,
                    is_reversal=True
                )
                new_ledger_item.save()
        else:
            ledger_item = models.LedgerItem.objects.filter(
                type=models.LedgerItem.TYPE_CHARGE,
                type_id=data["id"],
                is_reversal=False,
                state=models.LedgerItem.STATE_PENDING
            ).first()
            if ledger_item:
                ledger_item.state = models.LedgerItem.STATE_FAILED
                ledger_item.save()

        return HttpResponse(status=200)


@csrf_exempt
@require_POST
@idempotency_key(optional=True)
def subscribe_user(request, user_id):
    auth_error = check_api_auth(request)
    if auth_error:
        return auth_error

    user = get_user_model().objects.filter(username=user_id).first()
    account = user.account if user else None  # type: models.Account

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest()

    if "plan_id" not in data or "initial_usage" not in data:
        return HttpResponseBadRequest()

    can_reject = data.get("can_reject", True)
    off_session = data.get("off_session", True)
    plan = get_object_or_404(models.RecurringPlan, id=data["plan_id"])

    existing_subscription = models.Subscription.objects.filter(plan=plan, account=account).first()
    if existing_subscription:
        return HttpResponse(status=409)

    initial_units = int(data["initial_usage"])
    initial_charge = plan.calculate_charge(initial_units)
    subscription_usage_id = uuid.uuid4()
    now = timezone.now()

    with transaction.atomic():
        try:
            charge_state = tasks.charge_account(
                account, initial_charge, plan.name, f"su_{subscription_usage_id}",
                can_reject=can_reject, off_session=off_session,
                return_uri=data.get("return_uri")
            )
        except tasks.ChargeError as e:
            return HttpResponse(json.dumps({
                "message": e.message,
                "charge_state_id": str(e.charge_state.id)
            }), content_type='application/json', status=402)
        except tasks.ChargeStateRequiresActionError as e:
            return HttpResponse(json.dumps({
                "redirect_uri": e.redirect_url,
                "charge_state_id": str(e.charge_state.id)
            }), content_type='application/json', status=302)

        subscription = models.Subscription(
            plan=plan,
            account=account,
            last_billed=now,
            last_bill_attempted=now,
            state=models.Subscription.STATE_ACTIVE
        )
        subscription.save()
        subscription_usage = models.SubscriptionUsage(
            id=subscription_usage_id,
            subscription=subscription,
            timestamp=now,
            usage_units=initial_units
        )
        subscription_usage.save()

        return HttpResponse(json.dumps({
            "id": str(subscription.id),
            "charge_state_id": str(charge_state.id)
        }), content_type='application/json', status=200)


@csrf_exempt
@require_POST
@idempotency_key(optional=True)
def log_usage(request, subscription_id):
    auth_error = check_api_auth(request)
    if auth_error:
        return auth_error

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest()

    if "usage" not in data:
        return HttpResponseBadRequest()

    can_reject = data.get("can_reject", True)
    off_session = data.get("off_session", True)
    subscription = get_object_or_404(models.Subscription, id=subscription_id)
    old_usage = subscription.subscriptionusage_set.first()
    subscription_usage_id = uuid.uuid4()
    usage_units = int(data["usage"])
    now = timezone.now()

    if "timestamp" in data:
        now = datetime.datetime.utcfromtimestamp(data["timestamp"])

    with transaction.atomic():
        subscription_usage = models.SubscriptionUsage(
            id=subscription_usage_id,
            subscription=subscription,
            timestamp=now,
            usage_units=usage_units
        )
        subscription_usage.save()

        if subscription.plan.billing_type == models.RecurringPlan.TYPE_RECURRING:
            charge_diff = subscription.plan.calculate_charge(
                usage_units
            ) - subscription.plan.calculate_charge(
                old_usage.usage_units
            )

            charge_diff += subscription.amount_unpaid

            if charge_diff != 0:
                try:
                    tasks.charge_account(
                        subscription.account, charge_diff, subscription.plan.name,
                        f"sb_{subscription.id}" if subscription.amount_unpaid else f"su_{subscription_usage_id}",
                        can_reject=can_reject, off_session=off_session, return_uri=data.get("return_uri")
                    )
                except tasks.ChargeError as e:
                    return HttpResponse(json.dumps({
                        "message": e.message
                    }), content_type='application/json', status=402)
                except tasks.ChargeStateRequiresActionError as e:
                    return HttpResponse(json.dumps({
                        "redirect_uri": e.redirect_url,
                        "charge_state_id": str(e.charge_state.id)
                    }), content_type='application/json', status=302)

        return HttpResponse(status=200)


@csrf_exempt
@login_required
@require_POST
def save_subscription(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest()

    try:
        subscription_m = models.NotificationSubscription.objects.get(
            endpoint=body.get("endpoint")
        )
    except models.NotificationSubscription.DoesNotExist:
        subscription_m = models.NotificationSubscription()
        subscription_m.endpoint = body.get("endpoint")

    subscription_m.key_auth = body.get("keys", {}).get("auth")
    subscription_m.key_p256dh = body.get("keys", {}).get("p256dh")
    subscription_m.account = request.user.account
    subscription_m.save()

    return HttpResponse(status=200)


@login_required
@permission_required('billing.view_account', raise_exception=True)
def view_accounts(request):
    accounts = models.Account.objects.all()

    balances = models.LedgerItem.objects.filter(account=OuterRef('pk')) \
        .filter(state=models.LedgerItem.STATE_COMPLETED) \
        .order_by().values('account') \
        .annotate(balance=Sum('amount', output_field=DecimalField())) \
        .values('balance')
    total_balance = models.Account.objects \
                        .annotate(balance=Subquery(balances, output_field=DecimalField())) \
                        .aggregate(total_balance=Sum('balance')).get('total_balance') or decimal.Decimal(0)

    return render(request, "billing/accounts.html", {
        "accounts": accounts,
        "total_balance": total_balance
    })


@login_required
@permission_required('billing.view_account', raise_exception=True)
def view_account(request, account_id):
    user = get_object_or_404(get_user_model(), username=account_id)
    account = user.account  # type: models.Account
    cards = []

    if account.stripe_customer_id:
        cards = list(stripe.PaymentMethod.list(
            customer=account.stripe_customer_id,
            type="card"
        ).auto_paging_iter())

    def map_mandate(m):
        mandate = stripe.Mandate.retrieve(m.mandate_id)
        payment_method = stripe.PaymentMethod.retrieve(mandate["payment_method"])
        return {
            "id": m.id,
            "mandate_obj": m,
            "mandate": mandate,
            "payment_method": payment_method
        }

    bacs_mandates = list(map(map_mandate, models.BACSMandate.objects.filter(account=account)))
    sepa_mandates = list(map(map_mandate, models.SEPAMandate.objects.filter(account=account)))

    return render(request, "billing/account.html", {
        "account": account,
        "cards": cards,
        "bacs_mandates": bacs_mandates,
        "sepa_mandates": sepa_mandates,
    })


@login_required
@permission_required('billing.add_ledgeritem', raise_exception=True)
def charge_account(request, account_id):
    user = get_object_or_404(get_user_model(), username=account_id)
    account = user.account  # type: models.Account

    if request.method == "POST":
        form = forms.AccountChargeForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data['amount']
            descriptor = form.cleaned_data['descriptor']
            type_id = form.cleaned_data['id']
            can_reject = form.cleaned_data['can_reject']

            try:
                tasks.charge_account(account, amount, descriptor, type_id, can_reject=can_reject)
            except tasks.ChargeError as e:
                form.errors['__all__'] = (e.message,)
            else:
                return redirect('view_account', user.username)
    else:
        form = forms.AccountChargeForm()

    return render(request, "billing/account_charge.html", {
        "account": account,
        "form": form
    })


@login_required
@permission_required('billing.add_ledgeritem', raise_exception=True)
def manual_top_up_account(request, account_id):
    user = get_object_or_404(get_user_model(), username=account_id)
    account = user.account  # type: models.Account

    if request.method == "POST":
        form = forms.ManualTopUpForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data['amount']
            descriptor = form.cleaned_data['descriptor']

            ledger_item = models.LedgerItem(
                account=account,
                type=models.LedgerItem.TYPE_MANUAL,
                descriptor=descriptor,
                amount=amount,
                state=models.LedgerItem.STATE_COMPLETED,
            )
            ledger_item.save()
            return redirect('view_account', user.username)
    else:
        form = forms.ManualTopUpForm()

    return render(request, "billing/account_top_up.html", {
        "account": account,
        "form": form
    })


@login_required
@permission_required('billing.change_ledgeritem', raise_exception=True)
def edit_ledger_item(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, pk=item_id)

    if ledger_item.type == ledger_item.TYPE_BACS and ledger_item.state == ledger_item.STATE_PENDING:
        if request.method == "POST":
            form = forms.BACSMarkPaidForm(request.POST)
            if form.is_valid():
                ledger_item.amount = form.cleaned_data['amount']
                ledger_item.state = ledger_item.STATE_COMPLETED
                ledger_item.save()

                return redirect('view_account', ledger_item.account.user.username)
        else:
            form = forms.BACSMarkPaidForm()

        return render(request, "billing/account_bacs_mark_paid.html", {
            "form": form,
            "legder_item": ledger_item,
        })

    return redirect('view_account', ledger_item.account.user.username)
