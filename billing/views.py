from django.shortcuts import render, redirect, reverse, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseBadRequest
from django.core.exceptions import SuspiciousOperation
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.template import loader
from idempotency_key.decorators import idempotency_key
import decimal
import stripe
import stripe.error
import secrets
import json
import uuid
import csv
import urllib.parse
import django_keycloak_auth.clients
import keycloak.exceptions
import datetime
from . import forms, models, tasks


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
            if form.cleaned_data['method'] == forms.TopUpForm.METHOD_CARD:
                return redirect("top_up_card")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_BACS:
                return redirect("top_up_bacs")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_BACS_DIRECT_DEBIT:
                return redirect("top_up_bacs_direct_debit")
            # elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_SEPA_DIRECT_DEBIT:
            #     return redirect("top_up_sepa_direct_debit")
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
    else:
        form = forms.TopUpForm()

    return render(request, "billing/top_up.html", {
        "form": form
    })


@login_required
def top_up_card(request):
    account = request.user.account

    if "charge_state_id" in request.session:
        charge_state = get_object_or_404(models.ChargeState, id=request.session["charge_state_id"])
    else:
        charge_state = None

    cards = []
    if account.stripe_customer_id:
        cards = list(stripe.PaymentMethod.list(
            customer=account.stripe_customer_id,
            type="card"
        ).auto_paging_iter())

    if request.method != "POST":
        return render(request, "billing/top_up_card.html", {
            "is_new": False,
            "cards": cards
        })
    else:
        amount = decimal.Decimal(request.session.pop("amount"))
        charge_currency = request.POST.get("currency")
        if charge_currency not in ("eur", "gbp", "usd"):
            return HttpResponseBadRequest()

        amount_currency = models.ExchangeRate.get_rate('gbp', charge_currency) * amount
        amount_int = int(amount_currency * decimal.Decimal(100))
        if request.POST.get("card") == "new" or not cards:
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
        else:
            card_id = request.POST.get("card")
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
    account = request.user.account
    amount = decimal.Decimal(request.session.pop("amount"))
    ref = secrets.token_hex(9).upper()

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by bank transfer",
        amount=amount,
        type=models.LedgerItem.TYPE_BACS,
        type_id=ref,
        state=models.LedgerItem.STATE_COMPLETED if settings.IS_TEST else models.LedgerItem.STATE_PENDING
    )
    ledger_item.save()

    if settings.IS_TEST:
        return redirect('dashboard')
    else:
        return render(request, "billing/top_up_bacs.html", {
            "ref": ref,
            "amount": amount
        })


@login_required
def complete_top_up_bacs(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden

    if ledger_item.state != ledger_item.STATE_PENDING:
        return redirect('dashboard')

    if ledger_item.type != ledger_item.TYPE_BACS:
        return HttpResponseBadRequest

    return render(request, "billing/top_up_bacs.html", {
        "ref": ledger_item.type_id,
        "amount": ledger_item.amount
    })


@login_required
def top_up_bacs_direct_debit(request):
    account = request.user.account

    mandates = list(models.BACSMandate.objects.filter(account=account, active=True))

    if request.method != "POST" and mandates:
        def map_mandate(m):
            mandate = stripe.Mandate.retrieve(m.mandate_id)
            payment_method = stripe.PaymentMethod.retrieve(mandate["payment_method"])
            return {
                "id": m.id,
                "mandate": mandate,
                "payment_method": payment_method
            }

        return render(request, "billing/top_up_bacs_direct_debit.html", {
            "is_new": False,
            "mandates": list(map(map_mandate, mandates))
        })
    else:
        amount = decimal.Decimal(request.session.pop("amount"))
        amount_int = int(amount * decimal.Decimal(100))

        if request.POST.get("mandate") == "new" or not mandates:
            session = stripe.checkout.Session.create(
                payment_method_types=['bacs_debit'],
                line_items=[{
                    'price_data': {
                        'currency': 'gbp',
                        'product_data': {
                            'name': 'Top-up',
                        },
                        'unit_amount': amount_int,
                    },
                    'quantity': 1,
                }],
                mode='payment',
                customer=account.get_stripe_id(),
                payment_intent_data={
                    'setup_future_usage': 'off_session',
                },
                success_url=request.build_absolute_uri(reverse('dashboard')),
                cancel_url=request.build_absolute_uri(reverse('dashboard')),
            )

            ledger_item = models.LedgerItem(
                account=account,
                descriptor="Top-up by BACS Direct Debit",
                amount=amount,
                type=models.LedgerItem.TYPE_CHECKOUT,
                type_id=session["id"]
            )
            ledger_item.save()

            return render(request, "billing/top_up_bacs_direct_debit.html", {
                "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
                "checkout_id": session["id"],
                "is_new": True
            })
        else:
            mandate_id = request.POST.get("mandate")
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
def top_up_sepa_direct_debit(request):
    account = request.user.account  # type: models.Account

    mandates = list(models.SEPAMandate.objects.filter(account=account, active=True))

    if request.method != "POST" and mandates:
        def map_mandate(m):
            mandate = stripe.Mandate.retrieve(m.mandate_id)
            payment_method = stripe.PaymentMethod.retrieve(mandate["payment_method"])
            return {
                "id": m.id,
                "mandate": mandate,
                "payment_method": payment_method
            }

        return render(request, "billing/top_up_sepa_direct_debit.html", {
            "is_new": False,
            "mandates": list(map(map_mandate, mandates))
        })
    else:
        amount = decimal.Decimal(request.session.pop("amount"))
        amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
        amount_int = int(amount_eur * decimal.Decimal(100))

        if request.POST.get("mandate") == "new" or not mandates:
            payment_intent = stripe.PaymentIntent.create(
                amount=amount_int,
                currency='eur',
                description='Top-up',
                setup_future_usage='off_session',
                customer=account.get_stripe_id(),
                payment_method_types=['sepa_debit'],
                receipt_email=request.user.email,
            )

            ledger_item = models.LedgerItem(
                account=account,
                descriptor="Top-up by SEPA Direct Debit",
                amount=amount,
                type=models.LedgerItem.TYPE_SEPA,
                type_id=payment_intent["id"]
            )
            ledger_item.save()

            return render(request, "billing/top_up_sepa_direct_debit.html", {
                "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
                "client_secret": payment_intent["client_secret"],
                "is_new": True
            })
        else:
            mandate_id = request.POST.get("mandate")
            mandate = get_object_or_404(models.BACSMandate, id=mandate_id, active=True)

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

    if request.method == "POST":
        form = forms.SOFORTForm(request.POST)
        if form.is_valid():
            amount = decimal.Decimal(request.session.pop("amount"))

            amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
            amount_int = int(amount_eur * decimal.Decimal(100))
            source = stripe.Source.create(
                type='sofort',
                amount=amount_int,
                currency='eur',
                owner={
                    "email": request.user.email,
                    "name": f"{request.user.first_name} {request.user.last_name}"
                },
                redirect={
                    "return_url": request.build_absolute_uri(reverse('dashboard')),
                },
                sofort={
                    "country": form.cleaned_data['account_country'],
                },
                statement_descriptor="AS207960 Top-up"
            )

            ledger_item = models.LedgerItem(
                account=account,
                descriptor="Top-up by SOFORT",
                amount=amount,
                type=models.LedgerItem.TYPE_SOURCES,
                type_id=source['id']
            )
            ledger_item.save()

            return redirect(source["redirect"]["url"])
    else:
        form = forms.SOFORTForm()

    return render(request, "billing/top_up_sofort.html", {
        "form": form
    })


@login_required
def top_up_giropay(request):
    account = request.user.account

    if "charge_state_id" in request.session:
        charge_state = get_object_or_404(models.ChargeState, id=request.session.pop("charge_state_id"))
    else:
        charge_state = None

    amount = decimal.Decimal(request.session.pop("amount"))
    amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
    amount_int = int(amount_eur * decimal.Decimal(100))

    redirect_uri = reverse('complete_charge', args=(charge_state.id,)) if charge_state else reverse('dashboard')

    source = stripe.Source.create(
        type='giropay',
        amount=amount_int,
        currency='eur',
        owner={
            "email": request.user.email,
            "name": f"{request.user.first_name} {request.user.last_name}"
        },
        redirect={
            "return_url": request.build_absolute_uri(redirect_uri),
        },
        statement_descriptor="AS207960 Top-up"
    )

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by giropay",
        amount=amount,
        type=models.LedgerItem.TYPE_SOURCES,
        type_id=source['id']
    )
    ledger_item.save()
    if charge_state:
        charge_state.payment_ledger_item = ledger_item
        charge_state.save()

    return redirect(source["redirect"]["url"])


@login_required
def top_up_bancontact(request):
    account = request.user.account

    if "charge_state_id" in request.session:
        charge_state = get_object_or_404(models.ChargeState, id=request.session.pop("charge_state_id"))
    else:
        charge_state = None

    amount = decimal.Decimal(request.session.pop("amount"))
    amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
    amount_int = int(amount_eur * decimal.Decimal(100))

    redirect_uri = reverse('complete_charge', args=(charge_state.id,)) if charge_state else reverse('dashboard')

    source = stripe.Source.create(
        type='bancontact',
        amount=amount_int,
        currency='eur',
        owner={
            "email": request.user.email,
            "name": f"{request.user.first_name} {request.user.last_name}"
        },
        bancontact={
            "preferred_language": "en"
        },
        redirect={
            "return_url": request.build_absolute_uri(redirect_uri),
        },
        statement_descriptor="AS207960 Top-up"
    )

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by Bancontact",
        amount=amount,
        type=models.LedgerItem.TYPE_SOURCES,
        type_id=source['id']
    )
    ledger_item.save()
    if charge_state:
        charge_state.payment_ledger_item = ledger_item
        charge_state.save()

    return redirect(source["redirect"]["url"])


@login_required
def top_up_eps(request):
    account = request.user.account

    if "charge_state_id" in request.session:
        charge_state = get_object_or_404(models.ChargeState, id=request.session.pop("charge_state_id"))
    else:
        charge_state = None

    amount = decimal.Decimal(request.session.pop("amount"))
    amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
    amount_int = int(amount_eur * decimal.Decimal(100))

    redirect_uri = reverse('complete_charge', args=(charge_state.id,)) if charge_state else reverse('dashboard')

    source = stripe.Source.create(
        type='eps',
        amount=amount_int,
        currency='eur',
        owner={
            "email": request.user.email,
            "name": f"{request.user.first_name} {request.user.last_name}"
        },
        redirect={
            "return_url": request.build_absolute_uri(redirect_uri),
        },
        statement_descriptor="AS207960 Top-up"
    )

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by EPS",
        amount=amount,
        type=models.LedgerItem.TYPE_SOURCES,
        type_id=source['id']
    )
    ledger_item.save()
    if charge_state:
        charge_state.payment_ledger_item = ledger_item
        charge_state.save()

    return redirect(source["redirect"]["url"])


@login_required
def top_up_ideal(request):
    account = request.user.account

    if "charge_state_id" in request.session:
        charge_state = get_object_or_404(models.ChargeState, id=request.session.pop("charge_state_id"))
    else:
        charge_state = None

    amount = decimal.Decimal(request.session.pop("amount"))
    amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
    amount_int = int(amount_eur * decimal.Decimal(100))

    redirect_uri = reverse('complete_charge', args=(charge_state.id,)) if charge_state else reverse('dashboard')

    source = stripe.Source.create(
        type='ideal',
        amount=amount_int,
        currency='eur',
        owner={
            "email": request.user.email,
            "name": f"{request.user.first_name} {request.user.last_name}"
        },
        redirect={
            "return_url": request.build_absolute_uri(redirect_uri),
        },
        statement_descriptor="AS207960 Top-up"
    )

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by iDEAL",
        amount=amount,
        type=models.LedgerItem.TYPE_SOURCES,
        type_id=source['id']
    )
    ledger_item.save()
    if charge_state:
        charge_state.payment_ledger_item = ledger_item
        charge_state.save()

    return redirect(source["redirect"]["url"])


@login_required
def top_up_multibanco(request):
    account = request.user.account

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

    amount = decimal.Decimal(request.session.pop("amount"))
    amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
    amount_int = int(amount_eur * decimal.Decimal(100))

    redirect_uri = reverse('complete_charge', args=(charge_state.id,)) if charge_state else reverse('dashboard')

    source = stripe.Source.create(
        type='p24',
        amount=amount_int,
        currency='eur',
        owner={
            "email": request.user.email,
            "name": f"{request.user.first_name} {request.user.last_name}"
        },
        redirect={
            "return_url": request.build_absolute_uri(redirect_uri),
        },
        statement_descriptor="AS207960 Top-up"
    )

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by Przelewy24",
        amount=amount,
        type=models.LedgerItem.TYPE_SOURCES,
        type_id=source['id']
    )
    ledger_item.save()
    if charge_state:
        charge_state.payment_ledger_item = ledger_item
        charge_state.save()

    return redirect(source["redirect"]["url"])


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

    if ledger_item.state != ledger_item.STATE_PENDING:
        return redirect('dashboard')

    if ledger_item.type not in (
            ledger_item.TYPE_CARD, ledger_item.TYPE_BACS, ledger_item.TYPE_SOURCES, ledger_item.TYPE_CHECKOUT,
            ledger_item.TYPE_SEPA
    ):
        return HttpResponseBadRequest

    if ledger_item.type in (ledger_item.TYPE_CARD, ledger_item.TYPE_SEPA):
        payment_intent = stripe.PaymentIntent.retrieve(ledger_item.type_id)
        if payment_intent["status"] == "succeeded":
            ledger_item.state = ledger_item.STATE_COMPLETED
            ledger_item.save()
            return redirect('dashboard')
        stripe.PaymentIntent.cancel(ledger_item.type_id)
    elif ledger_item.type == ledger_item.TYPE_CHECKOUT:
        session = stripe.checkout.Session.retrieve(ledger_item.type_id)
        stripe.PaymentIntent.cancel(session["payment_intent"])

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
                        return redirect(charge_state.full_redirect_uri())
                    elif payment_intent["next_action"]["type"] == "redirect_to_url":
                        return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])
                if payment_intent["status"] != "succeeded":
                    try:
                        payment_intent.confirm()
                    except (stripe.error.CardError, stripe.error.InvalidRequestError) as e:
                        if isinstance(e, stripe.error.InvalidRequestError):
                            message = "Payment failed"
                        else:
                            message = e["error"]["message"]
                        charge_state.last_error = message
                        charge_state.save()

        if charge_state.ledger_item:
            if charge_state.ledger_item.state in (
                    models.LedgerItem.STATE_FAILED
            ):
                return redirect(charge_state.full_redirect_uri())

        if charge_state.payment_ledger_item:
            if charge_state.payment_ledger_item.type in (
                    models.LedgerItem.TYPE_CARD, models.LedgerItem.TYPE_SEPA
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
                update_from_charge(session, charge_state.payment_ledger_item)

            if charge_state.payment_ledger_item.state in (
                    models.LedgerItem.STATE_COMPLETED, models.LedgerItem.STATE_PROCESSING
            ):
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

    subscriptions = request.user.account.subscription_set.all()

    return render(request, "billing/account_details.html", {
        "account": account,
        "cards": cards,
        "bacs_mandates": bacs_mandates,
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
def add_bacs_mandate(request):
    account = request.user.account

    session = stripe.checkout.Session.create(
        payment_method_types=['bacs_debit'],
        mode='setup',
        customer=account.get_stripe_id(),
        success_url=request.build_absolute_uri(reverse('account_details')),
        cancel_url=request.build_absolute_uri(reverse('account_details')),
    )

    return render(request, "billing/top_up_bacs_direct_debit.html", {
        "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
        "checkout_id": session["id"],
        "is_new": True
    })


@login_required
@require_POST
def edit_bacs_mandate(request, m_id):
    mandate = get_object_or_404(models.BACSMandate, id=m_id)

    if mandate.account != request.user.account:
        return HttpResponseForbidden()

    action = request.POST.get("action")

    if action == "delete":
        stripe.PaymentMethod.detach(mandate.payment_method)
        mandate.delete()

    elif action == "default" and mandate.active:
        request.user.account.default_stripe_payment_method_id = mandate.payment_method
        request.user.account.save()

    return redirect('account_details')


@login_required
def add_sepa_mandate(request):
    account = request.user.account

    setup_intent = stripe.SetupIntent.create(
        payment_method_types=['sepa_debit'],
        customer=account.get_stripe_id(),
    )

    return render(request, "billing/top_up_sepa_direct_debit.html", {
        "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
        "client_secret": setup_intent["client_secret"],
        "is_new": True,
        "is_setup": True
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
                response['Content-Disposition'] =\
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
        elif event.type in ('charge.pending', 'charge.succeeded', 'charge.failed', 'charge.succeeded'):
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


def update_from_payment_intent(payment_intent, ledger_item=None):
    ledger_item = models.LedgerItem.objects.filter(
        Q(type=models.LedgerItem.TYPE_CARD) | Q(type=models.LedgerItem.TYPE_SEPA) &
        Q(type_id=payment_intent['id'])
    ).first() if not ledger_item else ledger_item

    if not ledger_item:
        return

    for charge in payment_intent["charges"]["data"]:
        if charge["payment_method_details"]["type"] == "sepa_debit":
            models.SEPAMandate.sync_mandate(
                charge["payment_method_details"]["sepa_debit"]["mandate"],
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

    ledger_item = models.LedgerItem.objects.filter(
        type=models.LedgerItem.TYPE_CHARGES,
        type_id=charge['id']
    ).first() if not ledger_item else ledger_item

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
                type_id__contains=ref,
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
            is_reversal=False
        ).first()
        if ledger_item:
            reversal_ledger_item = models.LedgerItem.objects.filter(
                type=models.LedgerItem.TYPE_CHARGE,
                type_id=data["id"],
                is_reversal=True
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

    return render(request, "billing/accounts.html", {
        "accounts": accounts
    })


@login_required
@permission_required('billing.view_account', raise_exception=True)
def view_account(request, account_id):
    user = get_object_or_404(get_user_model(), username=account_id)
    account = user.account  # type: models.Account
    cards = []

    if account.stripe_customer_id:
        cards = stripe.PaymentMethod.list(
            customer=account.stripe_customer_id,
            type="card"
        ).auto_paging_iter()

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
