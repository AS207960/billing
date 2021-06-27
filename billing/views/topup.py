import decimal
import enum
import secrets
import typing

import django_countries
import gocardless_pro.errors
import schwifty
import stripe
import stripe.error
import urllib.parse
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, HttpResponseForbidden, Http404
from django.shortcuts import get_object_or_404, redirect, render, reverse
import django.core.exceptions

from .. import forms, models, utils, tasks, vat
from ..apps import gocardless_client


class HandlePaymentOutcome(enum.Enum):
    CANCEL = enum.auto()
    FORBIDDEN = enum.auto()
    REDIRECT = enum.auto()
    DONE = enum.auto()
    RENDER = enum.auto()


def handle_payment(
        request, account: models.Account,
        charge_state: typing.Optional[models.ChargeState] = None,
):
    if request.method == "POST":
        if request.POST.get("action") == "cancel":
            if "selected_payment_method" in request.session:
                del request.session["selected_payment_method"]
            if "selected_currency" in request.session:
                del request.session["selected_currency"]
            if "top_up_amount" in request.session:
                del request.session["top_up_amount"]

            return HandlePaymentOutcome.CANCEL, None
        if request.POST.get("action") == "set_amount":
            top_up_amount = decimal.Decimal(request.POST.get("amount"))
            if top_up_amount >= 2:
                request.session["top_up_amount"] = str(top_up_amount)
        elif request.POST.get("action") == "set_payment_method":
            request.session["selected_payment_method"] = f"{request.POST.get('type')};{request.POST.get('id')}"
        elif request.POST.get("action") == "set_currency":
            request.session["selected_currency"] = request.POST.get("currency")

    if charge_state is None:
        charge_descriptor = None
        is_top_up = True
        charged_amount = decimal.Decimal(request.session["top_up_amount"]) if "top_up_amount" in request.session \
            else decimal.Decimal(0)
    else:
        is_top_up = False
        charged_amount = charge_state.amount
        charge_descriptor = charge_state.ledger_item.descriptor
    charged_amount_ext_vat = charged_amount

    has_vat = False
    vat_rate = decimal.Decimal(0)
    vat_charged = decimal.Decimal(0)
    available_currencies = []
    mandate_acceptance = False
    currency_name_lookup = {
        "gbp": "Pound Sterling",
        "eur": "Euro",
        "usd": "United States Dollar",
        "cad": "Canadian Dollar",
        "dkk": "Danish Krona",
        "sek": "Swedish Krona",
        "aud": "Australian Dollar",
        "nzd": "New Zealand Dollar",
        "huf": "Hungarian Florint",
        "ron": "Romanian Leu",
        "sgd": "Singapore Dollar",
        "try": "Turkish Lira",
    }
    selected_currency = request.session.get("selected_currency")
    billing_address_country = account.billing_address.country_code.code.lower()
    billing_country_name = dict(django_countries.countries)[billing_address_country.upper()]

    if account.taxable and not is_top_up:
        country_vat_rate = vat.get_vat_rate(billing_address_country, account.billing_address.postal_code)
        if country_vat_rate is not None:
            has_vat = True
            vat_rate = country_vat_rate
            vat_charged = (charged_amount * country_vat_rate)
            charged_amount += vat_charged

    if not is_top_up:
        from_account_balance = min(charge_state.account.balance, charged_amount)
        to_be_paid = charged_amount - from_account_balance
        needs_payment = to_be_paid > 0
    else:
        from_account_balance = None
        to_be_paid = charged_amount
        needs_payment = True

    cards = []
    ach_mandates = []
    autogiro_mandates = []
    bacs_mandates = []
    becs_mandates = []
    becs_nz_mandates = []
    betalingsservice_mandates = []
    pad_mandates = []
    sepa_mandates = []
    selected_payment_method_type = None
    selected_payment_method_id = None
    to_be_charged = None
    climate_contribution = False

    if needs_payment:
        if not request.session.get("selected_payment_method"):
            if account.default_stripe_payment_method_id:
                selected_payment_method_type = "stripe_pm"
                selected_payment_method_id = account.default_stripe_payment_method_id
                request.session["selected_payment_method"] = f"stripe_pm;{account.default_stripe_payment_method_id}"
            elif account.default_ach_mandate:
                selected_payment_method_type = "ach_mandate_gc"
                selected_payment_method_id = account.default_ach_mandate.id
                request.session["selected_payment_method"] = f"ach_mandate_gc;{account.default_ach_mandate.id}"
            elif account.default_autogiro_mandate:
                selected_payment_method_type = "autogiro_mandate_gc"
                selected_payment_method_id = account.default_autogiro_mandate.id
                request.session["selected_payment_method"] = f"ach_mandate_gc;{account.default_autogiro_mandate.id}"
            elif account.default_bacs_mandate:
                selected_payment_method_type = "bacs_mandate_stripe"
                selected_payment_method_id = account.default_bacs_mandate.id
                request.session["selected_payment_method"] = f"bacs_mandate_stripe;{account.default_bacs_mandate.id}"
            elif account.default_gc_bacs_mandate:
                selected_payment_method_type = "bacs_mandate_gc"
                selected_payment_method_id = account.default_gc_bacs_mandate.id
                request.session["selected_payment_method"] = f"bacs_mandate_gc;{account.default_gc_bacs_mandate.id}"
            elif account.default_becs_mandate:
                selected_payment_method_type = "becs_mandate_gc"
                selected_payment_method_id = account.default_becs_mandate.id
                request.session["selected_payment_method"] = f"becs_mandate_gc;{account.default_becs_mandate.id}"
            elif account.default_becs_nz_mandate:
                selected_payment_method_type = "becs_nz_mandate_gc"
                selected_payment_method_id = account.default_becs_nz_mandate.id
                request.session["selected_payment_method"] = f"becs_nz_mandate_gc;{account.default_becs_nz_mandate.id}"
            elif account.default_betalingsservice_mandate:
                selected_payment_method_type = "betalingsservice_mandate_gc"
                selected_payment_method_id = account.default_betalingsservice_mandate.id
                request.session["selected_payment_method"] = \
                    f"betalingsservice_mandate_gc;{account.default_betalingsservice_mandate.id}"
            elif account.default_pad_mandate:
                selected_payment_method_type = "pad_mandate_gc"
                selected_payment_method_id = account.default_pad_mandate.id
                request.session["selected_payment_method"] = f"pad_mandate_gc;{account.default_pad_mandate.id}"
            elif account.default_sepa_mandate:
                selected_payment_method_type = "sepa_mandate_stripe"
                selected_payment_method_id = account.default_sepa_mandate.id
                request.session["selected_payment_method"] = f"sepa_mandate_gc;{account.default_sepa_mandate.id}"
            elif account.default_gc_sepa_mandate:
                selected_payment_method_type = "sepa_mandate_gc"
                selected_payment_method_id = account.default_gc_sepa_mandate.id
                request.session["selected_payment_method"] = f"sepa_mandate_gc;{account.default_gc_sepa_mandate.id}"
            else:
                selected_payment_method_type = None
                selected_payment_method_id = None
        else:
            selected_payment_method = request.session.get("selected_payment_method").split(";", 1)
            selected_payment_method_type = selected_payment_method[0]
            selected_payment_method_id = selected_payment_method[1] if len(selected_payment_method) > 1 else None

        if selected_payment_method_type:
            if selected_payment_method_type == "giropay":
                if billing_address_country == "de" or not account.taxable:
                    available_currencies = ['eur']
                    climate_contribution = True
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "bancontact":
                if billing_address_country == "be" or not account.taxable:
                    available_currencies = ['eur']
                    mandate_acceptance = True
                    climate_contribution = True
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "eps":
                if billing_address_country == "at" or not account.taxable:
                    available_currencies = ['eur']
                    climate_contribution = True
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "ideal":
                if billing_address_country == "nl" or not account.taxable:
                    available_currencies = ['eur']
                    mandate_acceptance = True
                    climate_contribution = True
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "p24":
                if billing_address_country == "pl" or not account.taxable:
                    available_currencies = ['eur']
                    climate_contribution = True
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "multibanco":
                if billing_address_country == "pt" or not account.taxable:
                    available_currencies = ['eur']
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "sofort":
                if billing_address_country in ("at", "be", "de", "it", "nl", "es") or not account.taxable:
                    available_currencies = ['eur']
                    mandate_acceptance = True
                    climate_contribution = True
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "sofort":
                if billing_address_country in ("at", "be", "de", "it", "nl", "es") or not account.taxable:
                    available_currencies = ['eur']
                    mandate_acceptance = True
                    climate_contribution = True
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "uk_instant_bank_transfer":
                if billing_address_country == "gb" or not account.taxable:
                    available_currencies = ['gbp']
                    mandate_acceptance = True
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "sepa_mandate_stripe":
                m = get_object_or_404(models.SEPAMandate, id=selected_payment_method_id)
                payment_method = stripe.PaymentMethod.retrieve(m.payment_method)
                if payment_method['customer'] != account.stripe_customer_id:
                    return HandlePaymentOutcome.FORBIDDEN, None
                method_country = utils.country_from_stripe_payment_method(payment_method)
                if method_country == billing_address_country or not account.taxable:
                    available_currencies = ['eur']
                    climate_contribution = True
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "bacs_mandate_stripe":
                m = get_object_or_404(models.BACSMandate, id=selected_payment_method_id)
                payment_method = stripe.PaymentMethod.retrieve(m.payment_method)
                if payment_method['customer'] != account.stripe_customer_id:
                    return HandlePaymentOutcome.FORBIDDEN, None
                method_country = utils.country_from_stripe_payment_method(payment_method)
                if method_country == billing_address_country or not account.taxable:
                    available_currencies = ['gbp']
                    climate_contribution = True
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "ach_mandate_gc":
                m = get_object_or_404(models.ACHMandate, id=selected_payment_method_id)
                if m.account != account:
                    return HandlePaymentOutcome.FORBIDDEN, None
                if billing_address_country == "us" or not account.taxable:
                    available_currencies = ['usd']
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "autogiro_mandate_gc":
                m = get_object_or_404(models.AutogiroMandate, id=selected_payment_method_id)
                if m.account != account:
                    return HandlePaymentOutcome.FORBIDDEN, None
                if billing_address_country == "se" or not account.taxable:
                    available_currencies = ['sek']
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "bacs_mandate_gc":
                m = get_object_or_404(models.GCBACSMandate, id=selected_payment_method_id)
                if m.account != account:
                    return HandlePaymentOutcome.FORBIDDEN, None
                if billing_address_country == "gb" or not account.taxable:
                    available_currencies = ['gbp']
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "becs_mandate_gc":
                m = get_object_or_404(models.BECSMandate, id=selected_payment_method_id)
                if m.account != account:
                    return HandlePaymentOutcome.FORBIDDEN, None
                if billing_address_country == "au" or not account.taxable:
                    available_currencies = ['aud']
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "becs_nz_mandate_gc":
                m = get_object_or_404(models.BECSNZMandate, id=selected_payment_method_id)
                if m.account != account:
                    return HandlePaymentOutcome.FORBIDDEN, None
                if billing_address_country == "nz" or not account.taxable:
                    available_currencies = ['nzd']
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "betalingsservice_mandate_gc":
                m = get_object_or_404(models.BetalingsserviceMandate, id=selected_payment_method_id)
                if m.account != account:
                    return HandlePaymentOutcome.FORBIDDEN, None
                if billing_address_country == "dk" or not account.taxable:
                    available_currencies = ['dkk']
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "pad_mandate_gc":
                m = get_object_or_404(models.PADMandate, id=selected_payment_method_id)
                if m.account != account:
                    return HandlePaymentOutcome.FORBIDDEN, None
                if billing_address_country == "ca" or not account.taxable:
                    available_currencies = ['cad']
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "sepa_mandate_gc":
                m = get_object_or_404(models.GCSEPAMandate, id=selected_payment_method_id)
                if m.account != account:
                    return HandlePaymentOutcome.FORBIDDEN, None
                mandate = gocardless_client.mandates.get(m.mandate_id)
                bank_account = gocardless_client.customer_bank_accounts.get(mandate.links.customer_bank_account)
                if bank_account.country_code.lower() == billing_address_country or not account.taxable:
                    available_currencies = ['eur']
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "stripe_pm":
                payment_method = stripe.PaymentMethod.retrieve(selected_payment_method_id)
                if payment_method['customer'] != account.stripe_customer_id:
                    return HandlePaymentOutcome.FORBIDDEN, None
                method_country = utils.country_from_stripe_payment_method(payment_method)
                known_payment_method, _ = models.KnownStripePaymentMethod.objects.update_or_create(
                    account=account, method_id=selected_payment_method_id,
                    defaults={
                        "country_code": method_country
                    }
                )
                if method_country == billing_address_country or not account.taxable:
                    if payment_method["type"] == "card":
                        available_currencies = ['gbp', 'eur', 'usd']
                    climate_contribution = True
                else:
                    selected_payment_method_type = None
                    selected_payment_method_id = None
            elif selected_payment_method_type == "bank_transfer":
                if selected_payment_method_id == "gbp":
                    available_currencies = ['gbp']
                elif selected_payment_method_id == "eur":
                    available_currencies = ['eur']
                elif selected_payment_method_id == "usd":
                    available_currencies = ['usd']
                elif selected_payment_method_id == "aud":
                    if billing_address_country == "au" or not account.taxable:
                        available_currencies = ['aud']
                    else:
                        selected_payment_method_type = None
                        selected_payment_method_id = None
                elif selected_payment_method_id == "nzd":
                    if billing_address_country == "nz" or not account.taxable:
                        available_currencies = ['nzd']
                    else:
                        selected_payment_method_type = None
                        selected_payment_method_id = None
                elif selected_payment_method_id == "huf":
                    if billing_address_country == "hu" or not account.taxable:
                        available_currencies = ['huf']
                    else:
                        selected_payment_method_type = None
                        selected_payment_method_id = None
                elif selected_payment_method_id == "ron":
                    if billing_address_country == "ro" or not account.taxable:
                        available_currencies = ['ron']
                    else:
                        selected_payment_method_type = None
                        selected_payment_method_id = None
                elif selected_payment_method_id == "sgd":
                    if billing_address_country == "sg" or not account.taxable:
                        available_currencies = ['sgd']
                    else:
                        selected_payment_method_type = None
                        selected_payment_method_id = None
                elif selected_payment_method_id == "try":
                    if billing_address_country == "tr" or not account.taxable:
                        available_currencies = ['try']
                    else:
                        selected_payment_method_type = None
                        selected_payment_method_id = None

        if not selected_currency:
            if len(available_currencies):
                selected_currency = available_currencies[0]
                request.session["selected_currency"] = available_currencies[0]
        else:
            if selected_currency not in available_currencies:
                if len(available_currencies):
                    selected_currency = available_currencies[0]
                    request.session["selected_currency"] = available_currencies[0]
                else:
                    selected_currency = None
                    request.session["selected_currency"] = None

        if selected_currency:
            to_be_charged = models.ExchangeRate.get_rate('gbp', selected_currency) * to_be_paid
        else:
            to_be_charged = None

        can_charge = selected_payment_method_type and to_be_charged

        if request.method == "POST":
            if request.POST.get("action") == "payment_requests_pay":
                amount_int = int(round(charged_amount * decimal.Decimal(100)))

                if "selected_payment_method" in request.session:
                    del request.session["selected_payment_method"]
                if "selected_currency" in request.session:
                    del request.session["selected_currency"]
                if "top_up_amount" in request.session:
                    del request.session["top_up_amount"]

                payment_method = stripe.PaymentMethod.retrieve(request.POST.get("stripe_pm"))
                payment_method.attach(customer=account.get_stripe_id())

                method_country = utils.country_from_stripe_payment_method(payment_method)
                known_payment_method, _ = models.KnownStripePaymentMethod.objects.update_or_create(
                    account=account, method_id=payment_method.id,
                    defaults={
                        "country_code": method_country
                    }
                )
                if method_country == billing_address_country or not account.taxable:
                    if charge_state:
                        charge_state.ready_to_complete = True
                        charge_state.ledger_item.amount = -charged_amount
                        charge_state.ledger_item.vat_rate = vat_rate
                        charge_state.ledger_item.country_code = billing_address_country
                        charge_state.ledger_item.eur_exchange_rate = models.ExchangeRate.get_rate("gbp", "eur")
                        charge_state.save()

                    ledger_item = models.LedgerItem(
                        account=account,
                        amount=to_be_paid,
                        country_code=billing_address_country,
                        evidence_billing_address=account.billing_address,
                        charged_amount=charged_amount,
                        payment_charge_state=charge_state,
                        descriptor=f"Card payment for {charge_descriptor}" if charge_descriptor else "Top-up by card",
                        type=models.LedgerItem.TYPE_CARD,
                    )
                    payment_intent = stripe.PaymentIntent.create(
                        amount=amount_int,
                        currency="GBP",
                        customer=account.get_stripe_id(),
                        description=charge_descriptor if charge_descriptor else "Top-up",
                        receipt_email=request.user.email,
                        statement_descriptor_suffix="Top-up",
                        payment_method=payment_method.id,
                        confirm=True,
                        return_url=request.build_absolute_uri(reverse('complete_top_up_card', args=(ledger_item.id,)))
                    )
                    if settings.STRIPE_CLIMATE:
                        ledger_item.stripe_climate_contribution = charged_amount * decimal.Decimal(
                            settings.STRIPE_CLIMATE_RATE)
                    ledger_item.type_id = payment_intent['id']
                    ledger_item.save()
                    tasks.update_from_payment_intent(payment_intent, ledger_item)

                    if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
                        return HandlePaymentOutcome.REDIRECT, \
                               (ledger_item, payment_intent["next_action"]["redirect_to_url"]["url"])

                    return HandlePaymentOutcome.DONE, ledger_item

            if request.POST.get("action") == "pay" and selected_payment_method_type:
                if charge_state:
                    charge_state.ready_to_complete = True
                    charge_state.ledger_item.amount = -charged_amount
                    charge_state.ledger_item.vat_rate = vat_rate
                    charge_state.ledger_item.country_code = billing_address_country
                    charge_state.ledger_item.eur_exchange_rate = models.ExchangeRate.get_rate("gbp", "eur")
                    charge_state.save()

                ledger_item = models.LedgerItem(
                    account=account,
                    amount=to_be_paid,
                    country_code=billing_address_country,
                    evidence_billing_address=account.billing_address,
                    payment_charge_state=charge_state,
                )
                if climate_contribution and settings.STRIPE_CLIMATE:
                    ledger_item.stripe_climate_contribution = charged_amount * decimal.Decimal(
                        settings.STRIPE_CLIMATE_RATE)

                amount_int = int(round(to_be_charged * decimal.Decimal(100)))
                if "selected_payment_method" in request.session:
                    del request.session["selected_payment_method"]
                if "selected_currency" in request.session:
                    del request.session["selected_currency"]
                if "top_up_amount" in request.session:
                    del request.session["top_up_amount"]

                if selected_payment_method_type == "stripe_pm":
                    payment_intent = stripe.PaymentIntent.create(
                        amount=amount_int,
                        currency=selected_currency,
                        customer=account.get_stripe_id(),
                        description=charge_descriptor if charge_descriptor else "Top-up",
                        receipt_email=request.user.email,
                        statement_descriptor_suffix="Top-up",
                        payment_method=selected_payment_method_id,
                        confirm=True,
                        return_url=request.build_absolute_uri(reverse('complete_top_up_card', args=(ledger_item.id,)))
                    )
                    ledger_item.descriptor = f"Card payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by card"
                    ledger_item.type = models.LedgerItem.TYPE_CARD
                    ledger_item.type_id = payment_intent['id']
                    ledger_item.save()
                    tasks.update_from_payment_intent(payment_intent, ledger_item)

                    if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
                        return HandlePaymentOutcome.REDIRECT, \
                               (ledger_item, payment_intent["next_action"]["redirect_to_url"]["url"])

                elif selected_payment_method_type == "sofort":
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
                                "country": billing_address_country.upper(),
                            }
                        },
                        return_url=request.build_absolute_uri(reverse('complete_top_up_card', args=(ledger_item.id,))),
                        customer=account.get_stripe_id(),
                        description=charge_descriptor if charge_descriptor else "Top-up",
                        setup_future_usage="off_session",
                        mandate_data={
                            "customer_acceptance": {
                                "type": "online",
                                "online": {
                                    "ip_address": str(utils.get_ip(request)),
                                    "user_agent": request.META["HTTP_USER_AGENT"]
                                }
                            }
                        }
                    )
                    ledger_item.descriptor = f"SOFORT payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by SOFORT"
                    ledger_item.type = models.LedgerItem.TYPE_SOFORT
                    ledger_item.type_id = payment_intent['id']
                    ledger_item.save()
                    tasks.update_from_payment_intent(payment_intent, ledger_item)

                    if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
                        return HandlePaymentOutcome.REDIRECT, \
                               (ledger_item, payment_intent["next_action"]["redirect_to_url"]["url"])

                elif selected_payment_method_type == "giropay":
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
                        return_url=request.build_absolute_uri(reverse('complete_top_up_card', args=(ledger_item.id,))),
                        customer=account.get_stripe_id(),
                        description=charge_descriptor if charge_descriptor else "Top-up",
                    )
                    ledger_item.descriptor = f"GIROPAY payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by GIROPAY"
                    ledger_item.type = models.LedgerItem.TYPE_GIROPAY
                    ledger_item.type_id = payment_intent['id']
                    ledger_item.save()
                    tasks.update_from_payment_intent(payment_intent, ledger_item)

                    if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
                        return HandlePaymentOutcome.REDIRECT, \
                               (ledger_item, payment_intent["next_action"]["redirect_to_url"]["url"])

                elif selected_payment_method_type == "bancontact":
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
                        return_url=request.build_absolute_uri(reverse('complete_top_up_card', args=(ledger_item.id,))),
                        customer=account.get_stripe_id(),
                        description=charge_descriptor if charge_descriptor else "Top-up",
                        setup_future_usage="off_session",
                        mandate_data={
                            "customer_acceptance": {
                                "type": "online",
                                "online": {
                                    "ip_address": str(utils.get_ip(request)),
                                    "user_agent": request.META["HTTP_USER_AGENT"]
                                }
                            }
                        }
                    )
                    ledger_item.descriptor = f"Bancontact payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by Bancontact"
                    ledger_item.type = models.LedgerItem.TYPE_BANCONTACT
                    ledger_item.type_id = payment_intent['id']
                    ledger_item.save()
                    tasks.update_from_payment_intent(payment_intent, ledger_item)

                    if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
                        return HandlePaymentOutcome.REDIRECT, \
                               (ledger_item, payment_intent["next_action"]["redirect_to_url"]["url"])

                elif selected_payment_method_type == "eps":
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
                        return_url=request.build_absolute_uri(reverse('complete_top_up_card', args=(ledger_item.id,))),
                        customer=account.get_stripe_id(),
                        description=charge_descriptor if charge_descriptor else "Top-up",
                    )
                    ledger_item.descriptor = f"EPS payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by EPS"
                    ledger_item.type = models.LedgerItem.TYPE_EPS
                    ledger_item.type_id = payment_intent['id']
                    ledger_item.save()
                    tasks.update_from_payment_intent(payment_intent, ledger_item)

                    if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
                        return HandlePaymentOutcome.REDIRECT, \
                               (ledger_item, payment_intent["next_action"]["redirect_to_url"]["url"])

                elif selected_payment_method_type == "ideal":
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
                        return_url=request.build_absolute_uri(reverse('complete_top_up_card', args=(ledger_item.id,))),
                        customer=account.get_stripe_id(),
                        description=charge_descriptor if charge_descriptor else "Top-up",
                        setup_future_usage="off_session",
                        mandate_data={
                            "customer_acceptance": {
                                "type": "online",
                                "online": {
                                    "ip_address": str(utils.get_ip(request)),
                                    "user_agent": request.META["HTTP_USER_AGENT"]
                                }
                            }
                        }
                    )
                    ledger_item.descriptor = f"iDEAL payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by iDEAL"
                    ledger_item.type = models.LedgerItem.TYPE_IDEAL
                    ledger_item.type_id = payment_intent['id']
                    ledger_item.save()
                    tasks.update_from_payment_intent(payment_intent, ledger_item)

                    if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
                        return HandlePaymentOutcome.REDIRECT, \
                               (ledger_item, payment_intent["next_action"]["redirect_to_url"]["url"])

                elif selected_payment_method_type == "p24":
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
                        return_url=request.build_absolute_uri(reverse('complete_top_up_card', args=(ledger_item.id,))),
                        customer=account.get_stripe_id(),
                        description=charge_descriptor if charge_descriptor else "Top-up",
                    )
                    ledger_item.descriptor = f"Przelewy24 payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by Prezelewy24"
                    ledger_item.type = models.LedgerItem.TYPE_P24
                    ledger_item.type_id = payment_intent['id']
                    ledger_item.save()
                    tasks.update_from_payment_intent(payment_intent, ledger_item)

                    if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
                        return HandlePaymentOutcome.REDIRECT, \
                               (ledger_item, payment_intent["next_action"]["redirect_to_url"]["url"])

                elif selected_payment_method_type == "multibanco":
                    source = stripe.Source.create(
                        type='multibanco',
                        amount=amount_int,
                        currency='eur',
                        owner={
                            "email": request.user.email,
                            "name": f"{request.user.first_name} {request.user.last_name}"
                        },
                        redirect={
                            "return_url": request.build_absolute_uri(
                                reverse('complete_top_up_sources', args=(ledger_item.id,)))
                        },
                        statement_descriptor="AS207960 Top-up"
                    )

                    ledger_item.descriptor = f"Multibanco payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by Multibanco"
                    ledger_item.type = models.LedgerItem.TYPE_SOURCES
                    ledger_item.type_id = source['id']
                    ledger_item.save()
                    tasks.update_from_source(source, ledger_item)
                    return HandlePaymentOutcome.REDIRECT, (ledger_item, source["redirect"]["url"])

                elif selected_payment_method_type == "bank_transfer_stripe":
                    if selected_payment_method_id == "gbp":
                        payment_intent = stripe.PaymentIntent.create(
                            amount=amount_int,
                            currency=selected_currency,
                            customer=account.get_stripe_id(),
                            description=charge_descriptor if charge_descriptor else "Top-up",
                            receipt_email=request.user.email,
                            payment_method_types=["customer_balance"],
                            payment_method_data={
                                "type": "customer_balance"
                            },
                            payment_method_options={
                                "customer_balance": {
                                    "funding_type": "bank_transfer",
                                    "bank_transfer": {
                                        "type": "gb_bank_account"
                                    }
                                }
                            },
                            confirm=True,
                        )
                        ledger_item.descriptor = f"Bank transfer for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by bank transfer"
                        ledger_item.type = models.LedgerItem.TYPE_STRIPE_BACS
                        ledger_item.state = models.LedgerItem.STATE_PENDING
                        ledger_item.type_id = payment_intent['id']
                        ledger_item.save()

                        return HandlePaymentOutcome.REDIRECT, (
                            ledger_item,
                            reverse('complete_top_up_bank_transfer_stripe', args=(ledger_item.id,))
                        )

                elif selected_payment_method_type == "bacs_mandate_stripe":
                    m = models.BACSMandate.objects.get(id=selected_payment_method_id)
                    payment_intent = stripe.PaymentIntent.create(
                        amount=amount_int,
                        currency=selected_currency,
                        customer=account.get_stripe_id(),
                        description=charge_descriptor if charge_descriptor else "Top-up",
                        receipt_email=request.user.email,
                        statement_descriptor_suffix="Top-up",
                        payment_method=m.payment_method,
                        payment_method_types=["bacs_debit"],
                        confirm=True,
                    )
                    ledger_item.descriptor = f"BACS Diect Debit payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by BACS Direct Debit"
                    ledger_item.type = models.LedgerItem.TYPE_CARD
                    ledger_item.state = models.LedgerItem.STATE_PROCESSING
                    ledger_item.type_id = payment_intent['id']
                    ledger_item.save()

                elif selected_payment_method_type == "sepa_mandate_stripe":
                    m = models.SEPAMandate.objects.get(id=selected_payment_method_id)
                    payment_intent = stripe.PaymentIntent.create(
                        amount=amount_int,
                        currency=selected_currency,
                        customer=account.get_stripe_id(),
                        description=charge_descriptor if charge_descriptor else "Top-up",
                        receipt_email=request.user.email,
                        statement_descriptor_suffix="Top-up",
                        payment_method=m.payment_method,
                        payment_method_types=["sepa_debit"],
                        confirm=True,
                    )
                    ledger_item.descriptor = f"SEPA Direct Debit payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by SEPA Direct Debit"
                    ledger_item.type = models.LedgerItem.TYPE_SEPA
                    ledger_item.state = models.LedgerItem.STATE_PROCESSING
                    ledger_item.type_id = payment_intent['id']
                    ledger_item.save()

                elif selected_payment_method_type == "ach_mandate_gc":
                    m = models.ACHMandate.objects.get(id=selected_payment_method_id)
                    payment = gocardless_client.payments.create(params={
                        "amount": amount_int,
                        "currency": "USD",
                        "description": charge_descriptor if charge_descriptor else "Top-up",
                        "retry_if_possible": False,
                        "links": {
                            "mandate": m.mandate_id
                        }
                    })

                    ledger_item.descriptor = f"ACH Direct Debit payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by ACH Direct Debit"
                    ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
                    ledger_item.type_id = payment.id
                    ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
                    ledger_item.evidence_ach_mandate = m
                    ledger_item.save()

                elif selected_payment_method_type == "autogiro_mandate_gc":
                    m = models.AutogiroMandate.objects.get(id=selected_payment_method_id)
                    payment = gocardless_client.payments.create(params={
                        "amount": amount_int,
                        "currency": "SEK",
                        "description": charge_descriptor if charge_descriptor else "Top-up",
                        "retry_if_possible": False,
                        "links": {
                            "mandate": m.mandate_id
                        }
                    })

                    ledger_item.descriptor = f"Autogiro payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by Autogiro"
                    ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
                    ledger_item.type_id = payment.id
                    ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
                    ledger_item.evidence_autogiro_mandate = m
                    ledger_item.save()

                elif selected_payment_method_type == "bacs_mandate_gc":
                    m = models.GCBACSMandate.objects.get(id=selected_payment_method_id)
                    payment = gocardless_client.payments.create(params={
                        "amount": amount_int,
                        "currency": "GBP",
                        "description": charge_descriptor if charge_descriptor else "Top-up",
                        "retry_if_possible": False,
                        "links": {
                            "mandate": m.mandate_id
                        }
                    })

                    ledger_item.descriptor = f"BACS Direct Debit payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by BACS Direct Debit"
                    ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
                    ledger_item.type_id = payment.id
                    ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
                    ledger_item.evidence_gc_bacs_mandate = m
                    ledger_item.save()

                elif selected_payment_method_type == "becs_mandate_gc":
                    m = models.BECSMandate.objects.get(id=selected_payment_method_id)
                    payment = gocardless_client.payments.create(params={
                        "amount": amount_int,
                        "currency": "AUD",
                        "description": charge_descriptor if charge_descriptor else "Top-up",
                        "retry_if_possible": False,
                        "links": {
                            "mandate": m.mandate_id
                        }
                    })

                    ledger_item.descriptor = f"BECS Direct Debit payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by BECS Direct Debit"
                    ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
                    ledger_item.type_id = payment.id
                    ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
                    ledger_item.evidence_becs_mandate = m
                    ledger_item.save()

                elif selected_payment_method_type == "becs_nz_mandate_gc":
                    m = models.BECSNZMandate.objects.get(id=selected_payment_method_id)
                    payment = gocardless_client.payments.create(params={
                        "amount": amount_int,
                        "currency": "NZD",
                        "description": charge_descriptor if charge_descriptor else "Top-up",
                        "retry_if_possible": False,
                        "links": {
                            "mandate": m.mandate_id
                        }
                    })

                    ledger_item.descriptor = f"BECS NZ Direct Debit payment for {charge_descriptor}"
                    ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
                    ledger_item.type_id = payment.id
                    ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
                    ledger_item.evidence_becs_nz_mandate = m
                    ledger_item.save()

                elif selected_payment_method_type == "betalingsservice_nz_mandate_gc":
                    m = models.BetalingsserviceMandate.objects.get(id=selected_payment_method_id)
                    payment = gocardless_client.payments.create(params={
                        "amount": amount_int,
                        "currency": "DKK",
                        "description": charge_descriptor if charge_descriptor else "Top-up",
                        "retry_if_possible": False,
                        "links": {
                            "mandate": m.mandate_id
                        }
                    })

                    ledger_item.descriptor = f"Betalingsservice payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by Betalingsservice"
                    ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
                    ledger_item.type_id = payment.id
                    ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
                    ledger_item.evidence_betalingsservice_mandate = m
                    ledger_item.save()

                elif selected_payment_method_type == "pad_mandate_gc":
                    m = models.PADMandate.objects.get(id=selected_payment_method_id)
                    payment = gocardless_client.payments.create(params={
                        "amount": amount_int,
                        "currency": "CAD",
                        "description": charge_descriptor if charge_descriptor else "Top-up",
                        "retry_if_possible": False,
                        "links": {
                            "mandate": m.mandate_id
                        }
                    })

                    ledger_item.descriptor = f"PAD Direct Debit payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by PAD Direct Debit"
                    ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
                    ledger_item.type_id = payment.id
                    ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
                    ledger_item.evidence_pad_mandate = m
                    ledger_item.save()

                elif selected_payment_method_type == "sepa_mandate_gc":
                    m = models.GCSEPAMandate.objects.get(id=selected_payment_method_id)
                    payment = gocardless_client.payments.create(params={
                        "amount": amount_int,
                        "currency": "EUR",
                        "description": charge_descriptor if charge_descriptor else "Top-up",
                        "retry_if_possible": False,
                        "links": {
                            "mandate": m.mandate_id
                        }
                    })

                    ledger_item.descriptor = f"SEPA Direct Debit payment for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by SEPA Direct Debit"
                    ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS
                    ledger_item.type_id = payment.id
                    ledger_item.state = models.LedgerItem.STATE_PROCESSING_CANCELLABLE
                    ledger_item.evidence_gc_sepa_mandate = m
                    ledger_item.save()

                elif selected_payment_method_type == "bank_transfer":
                    ref = secrets.token_hex(6).upper()
                    ledger_item.descriptor = f"Bank transfer for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by bank transfer"
                    ledger_item.type = models.LedgerItem.TYPE_BACS
                    ledger_item.type_id = ref
                    ledger_item.state = models.LedgerItem.STATE_PENDING
                    ledger_item.save()

                    return HandlePaymentOutcome.REDIRECT, (
                        ledger_item,
                        reverse('complete_top_up_bank_details', args=(ledger_item.id, selected_payment_method_id))
                    )

                elif selected_payment_method_type == "uk_instant_bank_transfer":
                    billing_request = gocardless_client.billing_requests.create(params={
                        "mandate_request": {
                            "currency": "GBP",
                        },
                        "payment_request": {
                            "amount": amount_int,
                            "currency": "GBP",
                            "description": charge_descriptor if charge_descriptor else "Top-up",
                        },
                        "links": {
                            "customer": account.get_gocardless_id()
                        }
                    })
                    if not account.gocardless_customer_id:
                        account.gocardless_customer_id = billing_request.links.customer
                        account.save()

                    ledger_item.descriptor = f"Instant bank transfer for {charge_descriptor}" \
                        if charge_descriptor else "Top-up by instant bank transfer"
                    ledger_item.type = models.LedgerItem.TYPE_GOCARDLESS_PR
                    ledger_item.type_id = billing_request.id
                    ledger_item.state = models.LedgerItem.STATE_PENDING
                    ledger_item.save()
                        
                    flow = gocardless_client.billing_request_flows.create(params={
                        "auto_fulfil": True,
                        "lock_customer_details": False,
                        "redirect_uri": request.build_absolute_uri(
                                reverse('complete_top_up_uk_instant_bank_transfer', args=(ledger_item.id,))
                        ),
                        "links": {
                            "billing_request": billing_request.id
                        }
                    })

                    return HandlePaymentOutcome.REDIRECT, (ledger_item, flow.authorisation_url)

                return HandlePaymentOutcome.DONE, ledger_item

        if account.stripe_customer_id:
            cards = list(map(lambda c: {
                "id": c.id,
                "billing_details": c.billing_details,
                "card": {
                    "brand": c.card.brand,
                    "exp_month": c.card.exp_month,
                    "exp_year": c.card.exp_year,
                    "last4": c.card.last4,
                    "country": dict(django_countries.countries)[c.card.country],
                    "country_emoji": chr(ord(c.card.country[0]) + 127397) + chr(ord(c.card.country[1]) + 127397)
                }
            }, filter(
                lambda c: (
                        c.card.country.lower() == account.billing_address.country_code.code.lower()
                        or not account.taxable
                ),
                stripe.PaymentMethod.list(
                    customer=account.stripe_customer_id,
                    type="card"
                ).auto_paging_iter()
            )))

        def map_sepa_mandate(m):
            mandate = stripe.Mandate.retrieve(m.mandate_id)
            payment_method = stripe.PaymentMethod.retrieve(mandate["payment_method"])
            return {
                "id": m.id,
                "type": "stripe",
                "selected": selected_payment_method_type == "sepa_mandate_stripe" and selected_payment_method_id == m.id,
                "country": payment_method["sepa_debit"]["country"],
                "cc": dict(django_countries.countries)[payment_method["sepa_debit"]["country"]],
                "cc_emoji": chr(ord(payment_method["sepa_debit"]["country"][0]) + 127397) +
                            chr(ord(payment_method["sepa_debit"]["country"][1]) + 127397),
                "last4": payment_method["sepa_debit"]["last4"],
                "bank": payment_method["sepa_debit"]["bank_code"],
                "ref": mandate["payment_method_details"]["sepa_debit"]["reference"],
            }

        def map_bacs_mandate(m):
            mandate = stripe.Mandate.retrieve(m.mandate_id)
            payment_method = stripe.PaymentMethod.retrieve(mandate["payment_method"])
            return {
                "id": m.id,
                "type": "stripe",
                "last4": payment_method["bacs_debit"]["last4"],
                "bank": payment_method["bacs_debit"]["sort_code"],
                "ref": mandate["payment_method_details"]["bacs_debit"]["reference"],
                "selected": selected_payment_method_type == "bacs_mandate_stripe" and selected_payment_method_id == m.id
            }

        def map_ach_mandate(m):
            mandate = gocardless_client.mandates.get(m.mandate_id)
            bank_account = gocardless_client.customer_bank_accounts.get(mandate.links.customer_bank_account)
            return {
                "id": m.id,
                "type": "gc",
                "selected": selected_payment_method_type == "ach_mandate_gc" and selected_payment_method_id == m.id,
                "last4": bank_account.account_number_ending,
                "account_type": bank_account.account_type,
                "bank": bank_account.bank_name,
                "ref": mandate.reference,
            }

        def map_gc_bacs_mandate(m, m_type):
            mandate = gocardless_client.mandates.get(m.mandate_id)
            bank_account = gocardless_client.customer_bank_accounts.get(mandate.links.customer_bank_account)
            return {
                "id": m.id,
                "type": "gc",
                "selected": selected_payment_method_type == f"{m_type}_mandate_gc" and selected_payment_method_id == m.id,
                "last4": bank_account.account_number_ending,
                "bank": bank_account.bank_name,
                "ref": mandate.reference,
            }

        def map_gc_sepa_mandate(m):
            mandate = gocardless_client.mandates.get(m.mandate_id)
            bank_account = gocardless_client.customer_bank_accounts.get(mandate.links.customer_bank_account)
            return {
                "id": m.id,
                "type": "gc",
                "selected": selected_payment_method_type == "sepa_mandate_gc" and selected_payment_method_id == m.id,
                "country": bank_account.country_code,
                "cc": dict(django_countries.countries)[bank_account.country_code],
                "cc_emoji": chr(ord(bank_account.country_code[0]) + 127397) +
                            chr(ord(bank_account.country_code[1]) + 127397),
                "last4": bank_account.account_number_ending,
                "bank": bank_account.bank_name,
                "ref": mandate.reference,
            }

        if account.billing_address.country_code.code.lower() == "us" or not account.taxable:
            ach_mandates = list(map(map_ach_mandate, models.ACHMandate.objects.filter(account=account, active=True)))
        if account.billing_address.country_code.code.lower() == "se" or not account.taxable:
            autogiro_mandates = list(map(
                lambda m: map_gc_bacs_mandate(m, "autogiro"),
                models.AutogiroMandate.objects.filter(account=account, active=True)
            ))
        if account.billing_address.country_code.code.lower() == "gb" or not account.taxable:
            bacs_mandates = list(map(map_bacs_mandate, models.BACSMandate.objects.filter(account=account, active=True)))
            bacs_mandates += list(map(
                lambda m: map_gc_bacs_mandate(m, "bacs"),
                models.GCBACSMandate.objects.filter(account=account, active=True)
            ))
        if account.billing_address.country_code.code.lower() == "au" or not account.taxable:
            becs_mandates = list(map(
                lambda m: map_gc_bacs_mandate(m, "becs"),
                models.BECSMandate.objects.filter(account=account, active=True)
            ))
        if account.billing_address.country_code.code.lower() == "nz" or not account.taxable:
            becs_nz_mandates = list(map(
                lambda m: map_gc_bacs_mandate(m, "becs_nz"),
                models.BECSNZMandate.objects.filter(account=account, active=True)
            ))
        if account.billing_address.country_code.code.lower() == "dk" or not account.taxable:
            betalingsservice_mandates = list(map(
                lambda m: map_gc_bacs_mandate(m, "betalingsservice"),
                models.BetalingsserviceMandate.objects.filter(account=account, active=True)
            ))
        if account.billing_address.country_code.code.lower() == "ca" or not account.taxable:
            pad_mandates = list(map(
                lambda m: map_gc_bacs_mandate(m, "pad"),
                models.PADMandate.objects.filter(account=account, active=True)
            ))
        sepa_mandates = list(map(map_sepa_mandate, models.SEPAMandate.objects.filter(account=account, active=True)))
        sepa_mandates += list(
            map(map_gc_sepa_mandate, models.GCSEPAMandate.objects.filter(account=account, active=True)))
        if account.taxable:
            sepa_mandates = list(filter(
                lambda m: m["country"] == account.billing_address.country_code.code.upper(), sepa_mandates
            ))
    else:
        can_charge = True
        if request.method == "POST" and request.POST.get("action") == "pay":
            if charge_state:
                charge_state.ledger_item.state = models.LedgerItem.STATE_COMPLETED
                charge_state.ledger_item.save()
            return HandlePaymentOutcome.DONE, None

    return HandlePaymentOutcome.RENDER, render(
        request,
        "billing/complete_order.html" if charge_state else "billing/top_up.html",
        {
            "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
            "cards": cards,
            "country": account.billing_address.country_code.code.lower(),
            "ach_mandates": ach_mandates,
            "autogiro_mandates": autogiro_mandates,
            "bacs_mandates": bacs_mandates,
            "becs_mandates": becs_mandates,
            "becs_nz_mandates": becs_nz_mandates,
            "betalingsservice_mandates": betalingsservice_mandates,
            "pad_mandates": pad_mandates,
            "sepa_mandates": sepa_mandates,
            "charge_amount": charged_amount_ext_vat,
            "charge_descriptor": charge_descriptor if charge_descriptor else "Top-up",
            "charged_amount": to_be_paid,
            "top_up": is_top_up,
            "from_account_balance": from_account_balance,
            "to_be_paid": charged_amount,
            "has_vat": has_vat,
            "vat_rate": vat_rate,
            "vat_charged": vat_charged,
            "billing_country_name": billing_country_name,
            "taxable": account.taxable,
            "selected_currency": selected_currency,
            "available_currencies": list(map(lambda c: (c, currency_name_lookup[c]), available_currencies)),
            "selected_payment_method_type": selected_payment_method_type,
            "selected_payment_method_id": selected_payment_method_id,
            "to_be_charged": to_be_charged,
            "mandate_acceptance": mandate_acceptance,
            "can_charge": can_charge,
            "needs_payment": needs_payment,
            "charge": charge_state,
            "climate_contribution": climate_contribution and settings.STRIPE_CLIMATE
        }
    )


@login_required
def top_up(request):
    account = request.user.account  # type: models.Account

    if not account.billing_address:
        return render(request, "billing/top_up_no_address.html")

    can_sell, can_sell_reason = account.can_sell
    if not can_sell:
        return render(request, "billing/top_up_cant_sell.html", {
            "reason": can_sell_reason,
            "account": account,
        })

    result, extra = handle_payment(request, account, None)
    if result == HandlePaymentOutcome.CANCEL:
        return redirect('dashboard')
    elif result == HandlePaymentOutcome.FORBIDDEN:
        return HttpResponseForbidden()
    elif result == HandlePaymentOutcome.REDIRECT:
        _ledger_item, url = extra
        return redirect(url)
    elif result == HandlePaymentOutcome.RENDER:
        return extra
    elif result == HandlePaymentOutcome.DONE:
        return redirect('dashboard')


@login_required
def complete_top_up_card(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden

    if ledger_item.type not in (
            ledger_item.TYPE_CARD, ledger_item.TYPE_SOFORT, ledger_item.TYPE_GIROPAY, ledger_item.TYPE_BANCONTACT,
            ledger_item.TYPE_EPS, ledger_item.TYPE_IDEAL
    ):
        return HttpResponseBadRequest()

    try:
        charge_state = ledger_item.charge_state_payment
    except django.core.exceptions.ObjectDoesNotExist:
        charge_state = None

    payment_intent = stripe.PaymentIntent.retrieve(ledger_item.type_id)
    tasks.update_from_payment_intent(payment_intent, ledger_item)

    if ledger_item.state != ledger_item.STATE_PENDING:
        if charge_state:
            return redirect('complete_order', charge_state.id)

        return redirect('dashboard')

    if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
        return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])

    if charge_state:
        return redirect('complete_order', charge_state.id)

    return redirect('dashboard')


@login_required
def top_up_bank_transfer(request):
    account = request.user.account  # type: models.Account

    referer = request.META.get("HTTP_REFERER")
    if referer:
        redirect_uri = referer
    else:
        redirect_uri = reverse('top_up')

    request.session["bank_transfer_redirect_uri"] = redirect_uri
    redirect_uri = urllib.parse.quote_plus(redirect_uri)

    if not account.billing_address:
        return render(request, "billing/top_up_no_address.html")

    billing_address_country = account.billing_address.country_code.code.lower()

    if request.method == "POST":
        if "iban" in request.POST:
            try:
                iban = schwifty.IBAN(request.POST.get("iban"))
            except ValueError as e:
                return render(request, "billing/top_up_bacs_search.html", {
                    "country": billing_address_country,
                    "taxable": account.taxable,
                    "iban": request.POST.get("iban"),
                    "iban_error": str(e),
                    "redirect_uri": redirect_uri,
                })

            if iban.country_code.lower() != billing_address_country:
                return render(request, "billing/top_up_bacs_search.html", {
                    "country": billing_address_country,
                    "taxable": account.taxable,
                    "iban": request.POST.get("iban"),
                    "iban_error": "IBAN country and billing address country must match.",
                    "redirect_uri": redirect_uri,
                })

            try:
                lookup = gocardless_client.bank_details_lookups.create(params={
                    "iban": iban.compact
                })
                if not len(lookup.available_debit_schemes):
                    return render(request, "billing/top_up_bacs_no_schemes.html", {
                        "country": billing_address_country,
                        "taxable": account.taxable,
                        "redirect_uri": redirect_uri,
                    })
                else:
                    return render(request, "billing/top_up_bacs_schemes.html", {
                        "country": billing_address_country,
                        "bank_name": lookup.bank_name,
                        "schemes": lookup.available_debit_schemes,
                        "taxable": account.taxable,
                        "redirect_uri": redirect_uri,
                    })
            except gocardless_pro.errors.ValidationFailedError as e:
                return render(request, "billing/top_up_bacs_no_schemes.html", {
                    "country": billing_address_country,
                    "taxable": account.taxable,
                    "redirect_uri": redirect_uri,
                })

    return render(request, "billing/top_up_bacs_search.html", {
        "country": billing_address_country,
        "taxable": account.taxable,
        "redirect_uri": redirect_uri,
    })


@login_required
def top_up_bank_transfer_local(request, country):
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

    redirect_uri = request.session.get("bank_transfer_redirect_uri", reverse("top_up"))

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
                        "country": country,
                        "redirect_uri": redirect_uri,
                    })
                else:
                    return render(request, "billing/top_up_bacs_schemes.html", {
                        "bank_name": lookup.bank_name,
                        "schemes": lookup.available_debit_schemes,
                        "country": country,
                        "redirect_uri": redirect_uri,
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
        "form": form,
        "redirect_uri": redirect_uri,
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
    elif currency == "try":
        amount = models.ExchangeRate.get_rate('gbp', 'try') * amount
        return render(request, "billing/top_up_bank_try.html", {
            "amount": amount,
            "ref": ref
        })
    else:
        raise Http404()


@login_required
def top_up_bank_details(request, currency):
    currency = currency.lower()
    if currency == "gbp_stripe":
        request.session["selected_payment_method"] = f"bank_transfer_stripe;gbp"
    else:
        request.session["selected_payment_method"] = f"bank_transfer;{currency}"

    return redirect(request.session.get("bank_transfer_redirect_uri", reverse("top_up")))


@login_required
def complete_top_up_bank_transfer(request, item_id):
    account = request.user.account  # type: models.Account

    if not account.billing_address:
        return render(request, "billing/top_up_no_address.html")

    billing_address_country = account.billing_address.country_code.code.lower()

    return render(request, "billing/top_up_complete_currency_select.html", {
        "id": item_id,
        "country": billing_address_country,
        "taxable": account.taxable,
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
    if currency == "gbp_stripe":
        raise NotImplemented()

    return show_bank_details(request, ledger_item.amount, ledger_item.type_id, currency)


@login_required
def complete_top_up_bank_transfer_stripe(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden

    if ledger_item.type != models.LedgerItem.TYPE_STRIPE_BACS:
        return HttpResponseBadRequest()

    try:
        charge_state = ledger_item.charge_state_payment
    except django.core.exceptions.ObjectDoesNotExist:
        charge_state = None

    payment_intent = stripe.PaymentIntent.retrieve(ledger_item.type_id)
    tasks.update_from_payment_intent(payment_intent, ledger_item)
    if ledger_item.state != ledger_item.STATE_PENDING:
        if charge_state:
            return redirect('complete_order', charge_state.id)

        return redirect('dashboard')

    if payment_intent["next_action"]["type"] != "display_bank_transfer_instructions":
        return redirect('dashboard')

    bank_instructions = payment_intent["next_action"]["display_bank_transfer_instructions"]
    amount_remaining = decimal.Decimal(bank_instructions["amount_remaining"]) / decimal.Decimal(100)
    if bank_instructions["type"] == "gb_bank_account":
        address = bank_instructions["financial_addresses"][0]["sort_code"]
        sort_code = address["sort_code"]
        account_info = {
            "sort_code": f"{sort_code[0:2]}-{sort_code[2:4]}-{sort_code[4:6]}",
            "account_number": address["account_number"],
            "type": "gb"
        }
    elif bank_instructions["type"] == "sort_code":
        sort_code = bank_instructions["sort_code"]["sort_code"]
        account_info = {
            "sort_code": f"{sort_code[0:2]}-{sort_code[2:4]}-{sort_code[4:6]}",
            "account_number": bank_instructions["sort_code"]["account_number"],
            "type": "gb"
        }
    else:
        return render(request, "billing/error.html", {
            "error": "Looks like something we didn't expect to happen, happened. Please contact us."
        })

    return render(request, "billing/top_up_bank_gbp_stripe.html", {
        "ledger_item": ledger_item,
        "bank_instructions": {
            "amount": amount_remaining,
            "currency": bank_instructions["currency"].upper(),
            "reference": bank_instructions["reference"],
            "account_info": account_info
        }
    })


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


# @login_required
# def complete_top_up_sepa_direct_debit(request, item_id):
#     ledger_item = get_object_or_404(models.LedgerItem, id=item_id)
#
#     if ledger_item.account != request.user.account:
#         return HttpResponseForbidden
#
#     if ledger_item.state != ledger_item.STATE_PENDING:
#         return redirect('dashboard')
#
#     if ledger_item.type != ledger_item.TYPE_SEPA:
#         return HttpResponseBadRequest
#
#     payment_intent = stripe.PaymentIntent.retrieve(ledger_item.type_id)
#
#     if payment_intent["status"] == "succeeded":
#         ledger_item.state = ledger_item.STATE_COMPLETED
#         ledger_item.save()
#         return redirect('dashboard')
#
#     return render(request, "billing/top_up_sepa_direct_debit.html", {
#         "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
#         "client_secret": payment_intent["client_secret"],
#         "is_new": True
#     })

@login_required
def complete_top_up_sources(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden

    try:
        charge_state = ledger_item.charge_state_payment
    except django.core.exceptions.ObjectDoesNotExist:
        charge_state = None

    if ledger_item.state != ledger_item.STATE_PENDING:
        if charge_state:
            return redirect('complete_order', charge_state.id)

        return redirect('dashboard')

    if ledger_item.type != ledger_item.TYPE_SOURCES:
        return HttpResponseBadRequest

    source = stripe.Source.retrieve(ledger_item.type_id)
    tasks.update_from_source(source, ledger_item)

    if source["status"] != "pending":
        if charge_state:
            return redirect('complete_order', charge_state.id)

        return redirect('dashboard')

    return redirect(source["redirect"]["url"])


@login_required
def complete_top_up_uk_instant_bank_transfer(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden

    try:
        charge_state = ledger_item.charge_state_payment
    except django.core.exceptions.ObjectDoesNotExist:
        charge_state = None

    if ledger_item.state != ledger_item.STATE_PENDING:
        if charge_state:
            return redirect('complete_order', charge_state.id)

        return redirect('dashboard')

    if ledger_item.type != ledger_item.TYPE_GOCARDLESS_PR:
        return HttpResponseBadRequest

    tasks.update_from_gc_billing_request(ledger_item.type_id, ledger_item)

    if ledger_item.state != ledger_item.STATE_PENDING:
        if charge_state:
            return redirect('complete_order', charge_state.id)

        return redirect('dashboard')

    flow = gocardless_client.billing_request_flows.create(params={
        "auto_fulfil": True,
        "lock_customer_details": False,
        "redirect_uri": request.build_absolute_uri(
            reverse('complete_top_up_uk_instant_bank_transfer', args=(ledger_item.id,))
        ),
        "links": {
            "billing_request": ledger_item.type_id
        }
    })

    return redirect(flow.authorisation_url)
