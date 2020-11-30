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

    billing_country_name = dict(django_countries.countries)[charge_state.country_code.upper()] \
        if charge_state.country_code else None
    has_vat = charge_state.vat_rate != 0
    vat_charged = charge_state.base_amount * charge_state.vat_rate
    order_total = charge_state.base_amount + vat_charged

    if not charge_state.payment_ledger_item:
        from_account_balance = order_total
    else:
        from_account_balance = order_total - charge_state.payment_ledger_item.amount

    left_to_be_paid = order_total - from_account_balance

    return render(request, "billing/order_details.html", {
        "charge": charge_state,
        "billing_country_name": billing_country_name,
        "has_vat": has_vat,
        "vat_charged": vat_charged,
        "order_total": order_total,
        "from_account_balance": from_account_balance,
        "left_to_be_paid": left_to_be_paid
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

    billing_addresses = models.AccountBillingAddress.objects.filter(account=charge_state.account, deleted=False)
    cards = []

    if request.method == "POST":
        if request.POST.get("action") == "set_billing_address":
            request.session["selected_billing_address_id"] = request.POST.get('id')
        elif request.POST.get("action") == "set_payment_method":
            request.session["selected_payment_method"] = f"{request.POST.get('type')};{request.POST.get('id')}"
        elif request.POST.get("action") == "set_currency":
            request.session["selected_currency"] = request.POST.get("currency")
        elif request.POST.get("action") == "cancel":
            if charge_state.ledger_item and charge_state.ledger_item.state != models.LedgerItem.STATE_COMPLETED:
                charge_state.ledger_item.state = models.LedgerItem.STATE_FAILED
                charge_state.ledger_item.save()

            return redirect(charge_state.full_redirect_uri())

    if not request.session.get("selected_billing_address_id"):
        default_billing_address = billing_addresses.filter(default=True).first()
        if default_billing_address:
            request.session["selected_billing_address_id"] = default_billing_address.id
            selected_billing_address_id = default_billing_address.id
            selected_billing_address = default_billing_address
        else:
            selected_billing_address_id = None
            selected_billing_address = None
    else:
        selected_billing_address_id = request.session.get("selected_billing_address_id")
        selected_billing_address = billing_addresses.filter(id=selected_billing_address_id).first()

    address_and_method_match = False
    has_vat = False
    vat_rate = decimal.Decimal(0)
    vat_charged = decimal.Decimal(0)
    taxable = not selected_billing_address.vat_id if selected_billing_address else True
    available_currencies = []
    mandate_acceptance = False
    currency_name_lookup = {
        "gbp": "Pound Sterling",
        "eur": "Euro",
        "usd": "United States Dollar",
        "aud": "Australian Dollar",
        "nzd": "New Zealand Dollar",
        "ron": "Romanian leu",
        "sgd": "Singapore Dollar",
    }
    selected_currency = request.session.get("selected_currency")
    billing_address_country = selected_billing_address.country_code.code.lower() \
        if bool(selected_billing_address) else None
    billing_country_name = dict(django_countries.countries)[billing_address_country.upper()] \
        if billing_address_country else None

    order_total = charge_state.base_amount

    if billing_address_country and taxable:
        country_vat_rate = vat.get_vat_rate(billing_address_country)
        if country_vat_rate is not None:
            has_vat = True
            vat_rate = country_vat_rate
            vat_charged = (order_total * country_vat_rate)
            order_total += vat_charged

    from_account_balance = min(charge_state.account.balance, order_total)
    left_to_be_paid = order_total - from_account_balance
    needs_payment = left_to_be_paid > 0
    account_evidence = {}

    if needs_payment:
        if left_to_be_paid < decimal.Decimal(1):
            left_to_be_paid = decimal.Decimal(1)

        if not request.session.get("selected_payment_method"):
            if charge_state.account.default_stripe_payment_method_id:
                selected_payment_method_type = "stripe_pm"
                selected_payment_method_id = charge_state.account.default_stripe_payment_method_id
                request.session["selected_payment_method"] = \
                    f"stripe_pm;{charge_state.account.default_stripe_payment_method_id}"
            else:
                selected_payment_method_type = None
                selected_payment_method_id = None
        else:
            selected_payment_method = request.session.get("selected_payment_method").split(";", 1)
            selected_payment_method_type = selected_payment_method[0]
            selected_payment_method_id = selected_payment_method[1] if len(selected_payment_method) > 1 else None

        address_and_method_selected = bool(selected_billing_address) and bool(selected_payment_method_type)
    else:
        selected_payment_method_type = None
        selected_payment_method_id = None
        if "selected_payment_method" in request.session:
            del request.session["selected_payment_method"]
        address_and_method_selected = True

    if address_and_method_selected:
        if needs_payment:
            method_country = None
            if selected_payment_method_type == "giropay":
                method_country = "de"
                available_currencies = ['eur']
            elif selected_payment_method_type == "bancontact":
                method_country = "be"
                available_currencies = ['eur']
                mandate_acceptance = True
            elif selected_payment_method_type == "eps":
                method_country = "at"
                available_currencies = ['eur']
            elif selected_payment_method_type == "ideal":
                method_country = "nl"
                available_currencies = ['eur']
                mandate_acceptance = True
            elif selected_payment_method_type == "p24":
                method_country = "pl"
                available_currencies = ['eur']
            elif selected_payment_method_type == "stripe_pm":
                payment_method = stripe.PaymentMethod.retrieve(selected_payment_method_id)
                if payment_method['customer'] != charge_state.account.stripe_customer_id:
                    return HttpResponseForbidden()
                method_country = utils.country_from_stripe_payment_method(payment_method)
                if payment_method["type"] == "card":
                    available_currencies = ['gbp', 'eur', 'usd', 'aud', 'nzd', 'ron', 'sgd']

            if vat.need_billing_evidence():
                address_and_method_match = billing_address_country == method_country
            else:
                address_and_method_match = True
        else:
            available_currencies = ['gbp']
            if vat.need_billing_evidence():
                account_evidence = tasks.find_account_evidence(charge_state.account, billing_address_country)
                address_and_method_match = account_evidence is not None
            else:
                address_and_method_match = True

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
        to_be_charged = models.ExchangeRate.get_rate('gbp', selected_currency) * left_to_be_paid
    else:
        to_be_charged = None

    can_charge = address_and_method_selected and address_and_method_match and (to_be_charged is not None)

    if can_charge and request.method == "POST" and request.POST.get("action") == "pay":
        charge_state.vat_rate = vat_rate
        charge_state.country_code = billing_address_country
        charge_state.ready_to_complete = True
        charge_state.evidence_billing_address = selected_billing_address
        charge_state.ledger_item.amount = -order_total
        charge_state.ledger_item.save()

        if needs_payment:
            amount_int = int(round(to_be_charged * decimal.Decimal(100)))

            payment_intent_options = {
                "confirm": True,
                "currency": selected_currency,
                "amount": amount_int,
                "statement_descriptor_suffix": "Top-up",
                "customer": charge_state.account.get_stripe_id(),
                "description": 'Top-up',
                "return_url": request.build_absolute_uri(request.path),
                "receipt_email": request.user.email,
            }
            ledger_item = models.LedgerItem(
                account=charge_state.account,
                amount=left_to_be_paid,
            )
            payment_intent = None

            if selected_payment_method_type == "giropay":
                payment_intent = stripe.PaymentIntent.create(
                    payment_method_types=["giropay"],
                    payment_method_data={
                        "type": "giropay",
                        "billing_details": {
                            "email": request.user.email,
                            "name": f"{request.user.first_name} {request.user.last_name}"
                        },
                    },
                    **payment_intent_options
                )
                ledger_item.descriptor = "Top-up by GIROPAY"
                ledger_item.type = models.LedgerItem.TYPE_GIROPAY
                ledger_item.type_id = payment_intent['id']
                ledger_item.save()
                charge_state.payment_ledger_item = ledger_item
                charge_state.save()

            elif selected_payment_method_type == "bancontact":
                payment_intent = stripe.PaymentIntent.create(
                    payment_method_types=["bancontact"],
                    payment_method_data={
                        "type": "bancontact",
                        "billing_details": {
                            "email": request.user.email,
                            "name": f"{request.user.first_name} {request.user.last_name}"
                        },
                    },
                    setup_future_usage="off_session",
                    mandate_data={
                        "customer_acceptance": {
                            "type": "online",
                            "online": {
                                "ip_address": str(utils.get_ip(request)),
                                "user_agent": request.META["HTTP_USER_AGENT"]
                            }
                        }
                    },
                    **payment_intent_options
                )
                ledger_item.descriptor = "Top-up by Bancontact"
                ledger_item.type = models.LedgerItem.TYPE_BANCONTACT
                ledger_item.type_id = payment_intent['id']
                ledger_item.save()
                charge_state.payment_ledger_item = ledger_item
                charge_state.save()

            elif selected_payment_method_type == "eps":
                payment_intent = stripe.PaymentIntent.create(
                    payment_method_types=["eps"],
                    payment_method_data={
                        "type": "eps",
                        "billing_details": {
                            "email": request.user.email,
                            "name": f"{request.user.first_name} {request.user.last_name}"
                        },
                    },
                    **payment_intent_options
                )
                ledger_item.descriptor = "Top-up by EPS"
                ledger_item.type = models.LedgerItem.TYPE_EPS
                ledger_item.type_id = payment_intent['id']
                ledger_item.save()
                charge_state.payment_ledger_item = ledger_item
                charge_state.save()

            elif selected_payment_method_type == "ideal":
                payment_intent = stripe.PaymentIntent.create(
                    payment_method_types=["ideal"],
                    payment_method_data={
                        "type": "ideal",
                        "billing_details": {
                            "email": request.user.email,
                            "name": f"{request.user.first_name} {request.user.last_name}"
                        },
                    },
                    setup_future_usage="off_session",
                    mandate_data={
                        "customer_acceptance": {
                            "type": "online",
                            "online": {
                                "ip_address": str(utils.get_ip(request)),
                                "user_agent": request.META["HTTP_USER_AGENT"]
                            }
                        }
                    },
                    **payment_intent_options
                )
                ledger_item.descriptor = "Top-up by iDEAL"
                ledger_item.type = models.LedgerItem.TYPE_IDEAL
                ledger_item.type_id = payment_intent['id']
                ledger_item.save()
                charge_state.payment_ledger_item = ledger_item
                charge_state.save()

            elif selected_payment_method_type == "p24":
                payment_intent = stripe.PaymentIntent.create(
                    payment_method_types=["p24"],
                    payment_method_data={
                        "type": "p24",
                        "billing_details": {
                            "email": request.user.email,
                            "name": f"{request.user.first_name} {request.user.last_name}"
                        },
                    },
                    **payment_intent_options
                )
                ledger_item.descriptor = "Top-up by Przelewy24"
                ledger_item.type = models.LedgerItem.TYPE_P24
                ledger_item.type_id = payment_intent['id']
                ledger_item.save()
                charge_state.payment_ledger_item = ledger_item
                charge_state.save()

            elif selected_payment_method_type == "stripe_pm":
                payment_intent = stripe.PaymentIntent.create(
                    payment_method=selected_payment_method_id,
                    **payment_intent_options
                )
                ledger_item.descriptor = "Top-up by card"
                ledger_item.type = models.LedgerItem.TYPE_CARD
                ledger_item.type_id = payment_intent['id']
                ledger_item.save()
                charge_state.payment_ledger_item = ledger_item
                charge_state.save()

            if payment_intent:
                if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
                    return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])

                tasks.update_from_payment_intent(payment_intent, ledger_item)
        else:
            for key in account_evidence:
                setattr(charge_state, key, account_evidence[key])
            charge_state.ledger_item.state = models.LedgerItem.STATE_COMPLETED
            charge_state.ledger_item.save()
            charge_state.save()

        if charge_state.ledger_item.state in (models.LedgerItem.STATE_FAILED, models.LedgerItem.STATE_COMPLETED):
            return redirect(charge_state.full_redirect_uri())
        elif charge_state.ledger_item.state == models.LedgerItem.STATE_PROCESSING:
            return render(request, "billing/order_processing.html", {
                "charge": charge_state
            })

    if charge_state.account.stripe_customer_id and needs_payment:
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
        }, stripe.PaymentMethod.list(
            customer=charge_state.account.stripe_customer_id,
            type="card"
        ).auto_paging_iter()))

    return render(request, "billing/complete_order.html", {
        "from_account_balance": from_account_balance,
        "left_to_be_paid": left_to_be_paid,
        "available_currencies": list(map(lambda c: (c, currency_name_lookup[c]), available_currencies)),
        "selected_currency": selected_currency,
        "to_be_charged": to_be_charged,
        "vat_charged": vat_charged,
        "has_vat": has_vat,
        "vat_rate": vat_rate,
        "taxable": taxable,
        "order_total": order_total,
        "billing_country_name": billing_country_name,
        "charge": charge_state,
        "cards": cards,
        "billing_addresses": billing_addresses,
        "selected_billing_address_id": selected_billing_address_id,
        "selected_payment_method_type": selected_payment_method_type,
        "selected_payment_method_id": selected_payment_method_id,
        "address_and_method_selected": address_and_method_selected,
        "address_and_method_match": address_and_method_match,
        "can_charge": can_charge,
        "needs_payment": needs_payment,
        "mandate_acceptance": mandate_acceptance
    })


# @login_required
# def complete_charge(request, charge_id):
#     charge_state = get_object_or_404(models.ChargeState, id=charge_id)
#
#     with transaction.atomic():
#         if not charge_state.account:
#             charge_state.account = request.user.account
#             charge_state.save()
#         if charge_state.ledger_item and not charge_state.ledger_item.account:
#             charge_state.ledger_item.account = request.user.account
#             charge_state.ledger_item.save()
#         if charge_state.payment_ledger_item and not charge_state.payment_ledger_item.account:
#             charge_state.payment_ledger_item.account = request.user.account
#             charge_state.payment_ledger_item.save()
#
#     if charge_state.account != request.user.account:
#         return HttpResponseForbidden()
#
#     if request.method == "POST":
#         if request.POST.get("action") == "cancel":
#             if charge_state.ledger_item and charge_state.ledger_item.state != models.LedgerItem.STATE_COMPLETED:
#                 charge_state.ledger_item.state = models.LedgerItem.STATE_FAILED
#                 charge_state.ledger_item.save()
#
#             return redirect(charge_state.full_redirect_uri())
#
#         form = forms.CompleteChargeForm(request.POST)
#         if form.is_valid():
#             request.session["charge_state_id"] = str(charge_state.id)
#             request.session["amount"] = str((charge_state.account.balance + charge_state.ledger_item.amount) * -1)
#             if form.cleaned_data['method'] == forms.TopUpForm.METHOD_CARD:
#                 return redirect("top_up_card")
#             elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_GIROPAY:
#                 return redirect("top_up_giropay")
#             elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_BANCONTACT:
#                 return redirect("top_up_bancontact")
#             elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_EPS:
#                 return redirect("top_up_eps")
#             elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_IDEAL:
#                 return redirect("top_up_ideal")
#             elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_P24:
#                 return redirect("top_up_p24")
#     else:
#         if charge_state.ledger_item and charge_state.account.balance >= (-charge_state.ledger_item.amount):
#             charge_state.ledger_item.state = charge_state.ledger_item.STATE_COMPLETED
#             charge_state.ledger_item.save()
#
#             return redirect(charge_state.full_redirect_uri())
#
#         payment_intent = stripe.PaymentIntent.retrieve(charge_state.payment_ledger_item.type_id) \
#             if (charge_state.payment_ledger_item and
#                 charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_CARD) \
#             else None
#
#         has_error = False
#         if payment_intent:
#             if payment_intent.get("last_payment_error"):
#                 charge_state.last_error = payment_intent["last_payment_error"]["message"] \
#                     if payment_intent["last_payment_error"]["type"] == "card_error" else "Payment failed"
#                 charge_state.save()
#                 has_error = True
#             else:
#                 if payment_intent["status"] == "requires_action":
#                     if payment_intent["next_action"]["type"] == "use_stripe_sdk":
#                         charge_state.payment_ledger_item.state = charge_state.payment_ledger_item.STATE_FAILED
#                         charge_state.payment_ledger_item.save()
#                         charge_state.last_error = "Card requires authentication"
#                         charge_state.save()
#                         has_error = True
#                     elif payment_intent["next_action"]["type"] == "redirect_to_url":
#                         return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])
#                 # if payment_intent["status"] != "succeeded":
#                 #     try:
#                 #         payment_intent.confirm()
#                 #     except (stripe.error.CardError, stripe.error.InvalidRequestError) as e:
#                 #         if isinstance(e, stripe.error.InvalidRequestError):
#                 #             message = "Payment failed"
#                 #         else:
#                 #             message = e["error"]["message"]
#                 #         charge_state.last_error = message
#                 #         charge_state.save()
#
#         if charge_state.ledger_item:
#             if charge_state.ledger_item.state in (
#                     models.LedgerItem.STATE_FAILED
#             ):
#                 return redirect(charge_state.full_redirect_uri())
#
#         if charge_state.payment_ledger_item:
#             if charge_state.payment_ledger_item.type in (
#                     models.LedgerItem.TYPE_CARD, models.LedgerItem.TYPE_SEPA, models.LedgerItem.TYPE_SOFORT,
#                     models.LedgerItem.TYPE_GIROPAY, models.LedgerItem.TYPE_BANCONTACT, models.LedgerItem.TYPE_EPS,
#                     models.LedgerItem.TYPE_IDEAL, models.LedgerItem.TYPE_P24
#             ):
#                 payment_intent = stripe.PaymentIntent.retrieve(charge_state.payment_ledger_item.type_id)
#                 tasks.update_from_payment_intent(payment_intent, charge_state.payment_ledger_item)
#             elif charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_SOURCES:
#                 source = stripe.Source.retrieve(charge_state.payment_ledger_item.type_id)
#                 tasks.update_from_source(source, charge_state.payment_ledger_item)
#             elif charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_CHARGES:
#                 charge = stripe.Charge.retrieve(charge_state.payment_ledger_item.type_id)
#                 tasks.update_from_charge(charge, charge_state.payment_ledger_item)
#             elif charge_state.payment_ledger_item.type == models.LedgerItem.TYPE_CHECKOUT:
#                 session = stripe.checkout.Session.retrieve(charge_state.payment_ledger_item.type_id)
#                 tasks.update_from_checkout_session(session, charge_state.payment_ledger_item)
#
#             if charge_state.payment_ledger_item.state in (
#                     models.LedgerItem.STATE_COMPLETED, models.LedgerItem.STATE_PROCESSING,
#                     models.LedgerItem.STATE_PROCESSING_CANCELLABLE
#             ):
#                 if charge_state.payment_ledger_item.state == models.LedgerItem.STATE_PROCESSING_CANCELLABLE:
#                     charge_state.payment_ledger_item.state = models.LedgerItem.STATE_PROCESSING
#                     charge_state.payment_ledger_item.save()
#                 if charge_state.ledger_item:
#                     charge_state.ledger_item.state = charge_state.ledger_item.STATE_COMPLETED
#                     charge_state.ledger_item.save()
#
#                 return redirect(charge_state.full_redirect_uri())
#             elif charge_state.ledger_item and not has_error:
#                 charge_state.last_error = "Payment failed."
#                 charge_state.save()
#         elif charge_state.ledger_item and not has_error:
#             charge_state.last_error = "Insufficient funds in your account."
#             charge_state.save()
#
#         form = forms.CompleteChargeForm()
#
#     return render(request, "billing/complete_charge.html", {
#         "charge": charge_state,
#         "form": form
#     })
