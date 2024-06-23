import decimal
import json

import django_countries
import requests
import stripe
import stripe.error
import django.core.validators
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import webhooks, topup, account, dashboard, admin, api, freeagent
from .. import forms, models, tasks, vat, utils


def sw(request):
    return render(request, "billing/js/sw.js", {}, content_type="application/javascript")


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
def order_details(request, charge_id):
    charge_state = get_object_or_404(models.ChargeState, id=charge_id)

    if charge_state.account != request.user.account:
        return HttpResponseForbidden()

    vat_number = None
    billing_country_name = dict(django_countries.countries)[charge_state.ledger_item.country_code.upper()] \
        if charge_state.ledger_item.country_code else None
    has_vat = charge_state.ledger_item.vat_rate != 0
    vat_charged = charge_state.amount * charge_state.ledger_item.vat_rate
    if charge_state.payment_ledger_item:
        from_account_balance = -(charge_state.ledger_item.amount + charge_state.payment_ledger_item.amount)
    else:
        from_account_balance = -charge_state.ledger_item.amount
    if charge_state.ledger_item.country_code:
        if charge_state.ledger_item.country_code.upper() in ("GB", "IM") and settings.OWN_UK_VAT_ID:
            vat_number = f"GB {settings.OWN_UK_VAT_ID}"
        elif charge_state.ledger_item.country_code.upper() == "TR" and settings.OWN_TR_VAT_ID:
            vat_number = f"TR {settings.OWN_TR_VAT_ID}"
        elif vat.get_vies_country_code(charge_state.ledger_item.country_code.upper()) is not None:
            vat_number = f"{settings.OWN_EU_VAT_COUNTRY} {settings.OWN_EU_VAT_ID}"

    return render(request, "billing/order_details.html", {
        "charge": charge_state,
        "billing_country_name": billing_country_name,
        "has_vat": has_vat,
        "vat_charged": vat_charged,
        "from_account_balance": from_account_balance,
        "reversal": charge_state.ledger_item.reversal,
        "vat_number": vat_number,
    })


@login_required
def top_up_details(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden()

    if ledger_item.type in (
            models.LedgerItem.TYPE_CARD, models.LedgerItem.TYPE_SEPA, models.LedgerItem.TYPE_SOFORT,
            models.LedgerItem.TYPE_GIROPAY, models.LedgerItem.TYPE_BANCONTACT, models.LedgerItem.TYPE_EPS,
            models.LedgerItem.TYPE_IDEAL, models.LedgerItem.TYPE_STRIPE_BACS,
    ):
        pi = stripe.PaymentIntent.retrieve(ledger_item.type_id, expand=["payment_method"])

        charge_descriptor = f"Charged to your {utils.descriptor_from_stripe_payment_method(pi['payment_method'])}"
    elif ledger_item.type == ledger_item.TYPE_BACS:
        if ledger_item.evidence_bank_account:
            charge_descriptor = f"Paid by bank transfer from a/n {ledger_item.evidence_bank_account.account_code}"
        else:
            charge_descriptor = "Paid by bank transfer"
    else:
        charge_descriptor = "Charged to your payment method"

    return render(request, "billing/top_up_details.html", {
        "ledger_item": ledger_item,
        "charge_descriptor": charge_descriptor
    })


@login_required
def top_up_details_crypto(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden()

    if ledger_item.type != models.LedgerItem.TYPE_CRYPTO:
        return HttpResponseForbidden()

    r = requests.get(f"https://api.commerce.coinbase.com/charges/{ledger_item.type_id}", headers={
        "X-CC-Api-Key": settings.COINBASE_API_KEY,
    })
    r.raise_for_status()
    data = r.json()

    return redirect(data["data"]["hosted_url"])

@login_required
def top_up_refund(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden()

    amount_refundable = ledger_item.amount_refundable
    if amount_refundable <= 0:
        return HttpResponseForbidden()

    if request.method == "POST":
        refund_form = forms.TopUpRefundForm(request.POST)
    else:
        refund_form = forms.TopUpRefundForm()

    refund_form.fields['amount'].validators.append(
        django.core.validators.MaxValueValidator(amount_refundable)
    )
    refund_form.fields['amount'].max_value = amount_refundable
    refund_form.fields['amount'].initial = amount_refundable

    if request.method == "POST" and refund_form.is_valid():
        tasks.process_ledger_item_refund(ledger_item, refund_form.cleaned_data['amount'])
        return redirect('dashboard')

    return render(request, "billing/top_up_refund.html", {
        "ledger_item": ledger_item,
        "amount_refundable": amount_refundable,
        "refund_form": refund_form
    })


@login_required
def complete_order(request, charge_id):
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

    if charge_state.payment_ledger_item:
        if charge_state.payment_ledger_item.type in (
                models.LedgerItem.TYPE_CARD, models.LedgerItem.TYPE_SEPA, models.LedgerItem.TYPE_SOFORT,
                models.LedgerItem.TYPE_GIROPAY, models.LedgerItem.TYPE_BANCONTACT, models.LedgerItem.TYPE_EPS,
                models.LedgerItem.TYPE_IDEAL, models.LedgerItem.TYPE_P24
        ):
            payment_intent = stripe.PaymentIntent.retrieve(charge_state.payment_ledger_item.type_id)
            tasks.update_from_payment_intent(payment_intent, charge_state.payment_ledger_item)
            if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
                return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])
        elif charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_SOURCES:
            source = stripe.Source.retrieve(charge_state.payment_ledger_item.type_id)
            tasks.update_from_source(source, charge_state.payment_ledger_item)
        elif charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_CHARGES:
            charge = stripe.Charge.retrieve(charge_state.payment_ledger_item.type_id)
            tasks.update_from_charge(charge, charge_state.payment_ledger_item)
        elif charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_CHECKOUT:
            session = stripe.checkout.Session.retrieve(charge_state.payment_ledger_item.type_id)
            tasks.update_from_checkout_session(session, charge_state.payment_ledger_item)

    if charge_state.ledger_item.state in (models.LedgerItem.STATE_FAILED, models.LedgerItem.STATE_COMPLETED):
        return redirect(charge_state.full_redirect_uri())
    elif charge_state.ledger_item.state == models.LedgerItem.STATE_PROCESSING:
        return render(request, "billing/order_processing.html", {
            "charge": charge_state
        })

    if not charge_state.account.billing_address:
        return render(request, "billing/top_up_no_address.html")

    can_sell, can_sell_reason = charge_state.account.can_sell
    if not can_sell:
        charge_state.ledger_item.state = models.LedgerItem.STATE_FAILED
        charge_state.ledger_item.save()
        charge_state.last_error = can_sell_reason
        charge_state.payment_ledger_item = None
        charge_state.save()
        return render(request, "billing/order_cant_sell.html", {
            "reason": can_sell_reason,
            "return_uri": charge_state.full_redirect_uri(),
        })

    result, extra = topup.handle_payment(
        request, charge_state.account, charge_state
    )
    if result == topup.HandlePaymentOutcome.CANCEL:
        charge_state.last_error = "Order cancelled"
        charge_state.save()
        if charge_state.ledger_item and charge_state.ledger_item.state != models.LedgerItem.STATE_COMPLETED:
            charge_state.ledger_item.state = models.LedgerItem.STATE_FAILED
            charge_state.ledger_item.save()

        return redirect(charge_state.full_redirect_uri())
    elif result == topup.HandlePaymentOutcome.FORBIDDEN:
        return HttpResponseForbidden()
    elif result == topup.HandlePaymentOutcome.REDIRECT:
        ledger_item, url = extra
        if ledger_item:
            charge_state.payment_ledger_item = ledger_item
            charge_state.save()
            ledger_item.save()
        return redirect(url)
    elif result == topup.HandlePaymentOutcome.RENDER:
        return extra
    elif result == topup.HandlePaymentOutcome.DONE:
        if extra:
            charge_state.payment_ledger_item = extra
            charge_state.save()
            extra.save()

        if charge_state.ledger_item.state == models.LedgerItem.STATE_PROCESSING:
            return render(request, "billing/order_processing.html", {
                "charge": charge_state
            })
        return redirect(charge_state.full_redirect_uri())
