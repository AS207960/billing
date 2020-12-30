import decimal
import json

import django_countries
import stripe
import stripe.error
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import webhooks, topup, account, dashboard, admin, api
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

    if charge_state.payment_ledger_item:
        billing_country_name = dict(django_countries.countries)[charge_state.payment_ledger_item.country_code.upper()] \
            if charge_state.payment_ledger_item.country_code else None
        has_vat = charge_state.payment_ledger_item.vat_rate != 0
        vat_charged = charge_state.payment_ledger_item.amount * charge_state.payment_ledger_item.vat_rate
        from_account_balance = -(charge_state.ledger_item.amount + charge_state.payment_ledger_item.amount)
        left_to_be_paid = charge_state.payment_ledger_item.amount
        charged_amount = charge_state.payment_ledger_item.amount + vat_charged
    else:
        has_vat = False
        vat_charged = 0
        billing_country_name = None
        from_account_balance = -charge_state.ledger_item.amount
        left_to_be_paid = 0
        charged_amount = 0

    return render(request, "billing/order_details.html", {
        "charge": charge_state,
        "billing_country_name": billing_country_name,
        "has_vat": has_vat,
        "vat_charged": vat_charged,
        "from_account_balance": from_account_balance,
        "left_to_be_paid": left_to_be_paid,
        "charged_amount": charged_amount,
    })


@login_required
def top_up_details(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden()

    billing_country_name = dict(django_countries.countries)[ledger_item.country_code.upper()] \
        if ledger_item.country_code else None
    has_vat = ledger_item.vat_rate != 0
    vat_charged = ledger_item.amount * ledger_item.vat_rate
    charged_amount = ledger_item.amount + vat_charged

    return render(request, "billing/top_up_details.html", {
        "ledger_item": ledger_item,
        "billing_country_name": billing_country_name,
        "has_vat": has_vat,
        "vat_charged": vat_charged,
        "charged_amount": charged_amount,
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

    from_account_balance = min(charge_state.account.balance, -charge_state.ledger_item.amount)
    left_to_be_paid = -(charge_state.ledger_item.amount + from_account_balance)

    result, extra = topup.handle_payment(
        request, charge_state.account, left_to_be_paid, from_account_balance, -charge_state.ledger_item.amount,
        charge_state, charge_state.ledger_item.descriptor
    )
    if result == topup.HandlePaymentOutcome.CANCEL:
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
