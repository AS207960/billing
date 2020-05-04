from django.shortcuts import render, redirect, reverse, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseBadRequest, HttpResponseNotFound
from django.conf import settings
from django.contrib.auth import get_user_model
import decimal
import stripe
import stripe.error
import secrets
import json
import django_keycloak_auth.clients
import keycloak.exceptions
from . import forms, models


@login_required
def dashboard(request):
    ledger_items = models.LedgerItem.objects.filter(account=request.user.account)

    return render(request, "billing/dashboard.html", {
        "ledger_items": ledger_items,
        "account": request.user.account
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

    return render(request, "billing/account_details.html", {
        "account": account,
        "cards": cards
    })


@login_required
def top_up(request):
    if request.method == "POST":
        form = forms.TopUpForm(request.POST)
        if form.is_valid():
            if form.cleaned_data['method'] == forms.TopUpForm.METHOD_CARD:
                return redirect(reverse("top_up_card") + f"?amount={form.cleaned_data['amount']}")
            elif form.cleaned_data['method'] == forms.TopUpForm.METHOD_BACS:
                return redirect(reverse("top_up_bacs") + f"?amount={form.cleaned_data['amount']}")

    else:
        form = forms.TopUpForm()

    return render(request, "billing/top_up.html", {
        "form": form
    })


@login_required
def top_up_card(request):
    account = request.user.account

    cards = []
    if account.stripe_customer_id:
        cards = list(stripe.PaymentMethod.list(
            customer=account.stripe_customer_id,
            type="card"
        ).auto_paging_iter())

    if cards and request.method != "POST":
        return render(request, "billing/top_up_card.html", {
            "is_new": False,
            "cards": cards
        })
    else:
        amount = decimal.Decimal(request.GET.get("amount"))
        amount_int = int(amount * decimal.Decimal(100))
        if request.POST.get("card") == "new" or not cards:
            payment_intent = stripe.PaymentIntent.create(
                amount=amount_int,
                currency='gbp',
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

            return render(request, "billing/top_up_card.html", {
                "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
                "client_secret": payment_intent["client_secret"],
                "customer_name": f"{request.user.first_name} {request.user.last_name}",
                "amount": amount_int,
                "is_new": True
            })
        else:
            card_id = request.POST.get("card")
            payment_method = stripe.PaymentMethod.retrieve(card_id)

            if payment_method['customer'] != request.user.account.stripe_customer_id:
                return HttpResponseForbidden()

            payment_intent = stripe.PaymentIntent.create(
                amount=amount_int,
                currency='gbp',
                customer=account.get_stripe_id(),
                description='Top-up',
                receipt_email=request.user.email,
                statement_descriptor_suffix="Top-up",
                payment_method=card_id,
                confirm=True,
                return_url=request.build_absolute_uri(reverse('dashboard'))
            )

            ledger_item = models.LedgerItem(
                account=account,
                descriptor="Top-up by card",
                amount=amount,
                type=models.LedgerItem.TYPE_CARD,
                type_id=payment_intent['id']
            )
            ledger_item.save()

            if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
                return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])

            return redirect('dashboard')


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
        return redirect('dashboard')

    amount_int = int(ledger_item.amount * decimal.Decimal(100))

    return render(request, "billing/top_up_card.html", {
        "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
        "client_secret": payment_intent["client_secret"],
        "customer_name": f"{request.user.first_name} {request.user.last_name}",
        "amount": amount_int,
        "is_new": True
    })


@login_required
def top_up_bacs(request):
    account = request.user.account
    amount = decimal.Decimal(request.GET.get("amount"))
    ref = secrets.token_hex(9).upper()

    ledger_item = models.LedgerItem(
        account=account,
        descriptor="Top-up by bank transfer",
        amount=amount,
        type=models.LedgerItem.TYPE_BACS,
        type_id=ref
    )
    ledger_item.save()

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
def fail_top_up(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden

    if ledger_item.state != ledger_item.STATE_PENDING:
        return HttpResponseBadRequest

    if ledger_item.type not in (ledger_item.TYPE_CARD, ledger_item.TYPE_BACS):
        return HttpResponseBadRequest

    if ledger_item.type == ledger_item.TYPE_CARD:
        stripe.PaymentIntent.cancel(ledger_item.type_id)

    ledger_item.state = models.LedgerItem.STATE_FAILED
    ledger_item.save()

    return redirect('dashboard')


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

    if event.type == 'payment_intent.succeeded':
        payment_intent = event.data.object
        payment_intent_succeeded(payment_intent)
    elif event.type == 'payment_intent.payment_failed':
        payment_intent = event.data.object
        payment_intent_failed(payment_intent)
    elif event.type == 'payment_intent.processing':
        payment_intent = event.data.object
        payment_intent_processing(payment_intent)
    else:
        return HttpResponseBadRequest()

    return HttpResponse(status=200)


def payment_intent_succeeded(payment_intent):
    ledger_item = models.LedgerItem.objects.filter(
        type=models.LedgerItem.TYPE_CARD,
        type_id=payment_intent['id']
    ).first()

    if not ledger_item:
        return

    ledger_item.amount = decimal.Decimal(payment_intent["amount_received"]) / decimal.Decimal(100)
    ledger_item.state = models.LedgerItem.STATE_COMPLETED
    ledger_item.save()


def payment_intent_failed(payment_intent):
    ledger_item = models.LedgerItem.objects.filter(
        type=models.LedgerItem.TYPE_CARD,
        type_id=payment_intent['id']
    ).first()

    if not ledger_item:
        return

    ledger_item.state = models.LedgerItem.STATE_FAILED
    ledger_item.save()


def payment_intent_processing(payment_intent):
    ledger_item = models.LedgerItem.objects.filter(
        type=models.LedgerItem.TYPE_CARD,
        type_id=payment_intent['id']
    ).first()

    if not ledger_item:
        return

    ledger_item.state = models.LedgerItem.STATE_PROCESSING
    ledger_item.save()


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
        ledger_item = models.LedgerItem.objects.filter(
            type=models.LedgerItem.TYPE_BACS,
            type_id=ref,
            state=models.LedgerItem.STATE_PENDING
        ).first()

        if ledger_item:
            ledger_item.amount = decimal.Decimal(data.get("amount")) / decimal.Decimal(100)
            ledger_item.state = models.LedgerItem.STATE_COMPLETED
            ledger_item.save()
    else:
        return HttpResponseBadRequest()

    return HttpResponse(status=200)


class ChargeError(Exception):
    def __init__(self, message):
        self.message = message


def attempt_charge_account(account: models.Account, amount: decimal.Decimal):
    if account.default_stripe_payment_method_id:
        amount_int = int(amount * decimal.Decimal(100))

        ledger_item = models.LedgerItem(
            account=account,
            descriptor="Top-up by card",
            amount=amount,
            type=models.LedgerItem.TYPE_CARD,
        )

        try:
            payment_intent = stripe.PaymentIntent.create(
                amount=amount_int,
                currency='gbp',
                customer=account.get_stripe_id(),
                description='Top-up',
                receipt_email=account.user.email,
                statement_descriptor_suffix="Top-up",
                payment_method=account.default_stripe_payment_method_id,
                confirm=True,
                off_session=True,
            )
        except stripe.error.CardError as e:
            err = e.error
            ledger_item.type_id = err.payment_intent['id']
            ledger_item.state = ledger_item.STATE_FAILED
            ledger_item.save()
            raise ChargeError(err.message)

        ledger_item.state = ledger_item.STATE_COMPLETED
        ledger_item.type_id = payment_intent['id']
        ledger_item.save()
    else:
        raise ChargeError("No card available to charge")

@csrf_exempt
@require_POST
def charge_user(request, user_id):
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

    user = get_user_model().objects.filter(username=user_id).first()
    if not user:
        return HttpResponseNotFound()

    account = user.account  # type: models.Account

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest()

    if "amount" not in data or "descriptor" not in data or "id" not in data:
        return HttpResponseBadRequest()

    can_reject = data.get("can_reject", True)

    try:
        amount = decimal.Decimal(data["amount"]) / decimal.Decimal(100)
    except decimal.InvalidOperation:
        return HttpResponseBadRequest()

    ledger_item = models.LedgerItem(
        account=account,
        descriptor=data["descriptor"],
        amount=-amount,
        type=models.LedgerItem.TYPE_CHARGE,
        type_id=data["id"]
    )
    ledger_item.save()

    if account.balance - amount < 0:
        charge_amount = -(account.balance - amount)
        try:
            attempt_charge_account(account, charge_amount)
        except ChargeError as e:
            if can_reject:
                ledger_item.state = ledger_item.STATE_FAILED
                ledger_item.save()
                return HttpResponse(json.dumps({
                    "message": e.message
                }), content_type='application/json', status=402)

    ledger_item.state = ledger_item.STATE_COMPLETED
    ledger_item.save()
    return HttpResponse(status=200)

