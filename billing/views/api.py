import datetime
import decimal
import json
import uuid

import django_keycloak_auth.clients
import keycloak.exceptions
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from idempotency_key.decorators import idempotency_key
from .. import models, tasks


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
                return_uri=data.get("return_uri"), supports_delayed=True
            )
        except tasks.ChargeError as e:
            return HttpResponse(json.dumps({
                "message": e.message,
                "charge_state_id": str(e.charge_state.id),
                "redirect_uri": e.redirect_url,
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

    status = charge_state.ledger_item.state

    if status == models.LedgerItem.STATE_PENDING:
        status = "pending"
    elif status in (models.LedgerItem.STATE_PROCESSING, models.LedgerItem.STATE_PROCESSING_CANCELLABLE):
        status = "processing"
    elif status == models.LedgerItem.STATE_FAILED:
        status = "failed"
    elif status == models.LedgerItem.STATE_COMPLETED:
        status = "completed"
    else:
        status = "unknown"

    return HttpResponse(json.dumps({
        "status": status,
        "redirect_uri": settings.EXTERNAL_URL_BASE + reverse('complete_order', args=(charge_state.id,)),
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
            reversal_ledger_item = ledger_item.reversal
            if not reversal_ledger_item:
                new_ledger_item = models.LedgerItem(
                    account=ledger_item.account,
                    descriptor=ledger_item.descriptor,
                    amount=-ledger_item.amount,
                    type=models.LedgerItem.TYPE_CHARGE,
                    reversal_for=ledger_item,
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

    if account:
        existing_subscription = models.Subscription.objects.filter(plan=plan, account=account).first()
        if existing_subscription:
            return HttpResponse(status=409)

    initial_units = int(data["initial_usage"])
    initial_charge = plan.calculate_charge(initial_units)
    subscription_usage_id = uuid.uuid4()
    now = timezone.now()

    with transaction.atomic():
        subscription = models.Subscription(
            plan=plan,
            account=account,
            last_billed=now,
            state=models.Subscription.STATE_PENDING
        )
        subscription.save()
        subscription_usage = models.SubscriptionUsage(
            id=subscription_usage_id,
            subscription=subscription,
            timestamp=now,
            usage_units=initial_units
        )
        subscription_usage.save()

        subscription_charge = models.SubscriptionCharge(
            subscription=subscription,
            timestamp=now,
            last_bill_attempted=now,
            failed_bill_attempts=0,
            amount=initial_charge,
            is_setup_charge=True
        )

        redirect_url = None
        try:
            charge_state = tasks.charge_account(
                subscription.account, initial_charge, plan.name, f"sb_{subscription.id}",
                can_reject=can_reject, off_session=off_session,
                return_uri=data.get("return_uri"), supports_delayed=True
            )
        except tasks.ChargeError as e:
            e.charge_state.ledger_item.subscription_charge = subscription_charge
            subscription_charge.last_ledger_item = e.charge_state.ledger_item
            ledger_item = e.charge_state.ledger_item
        except tasks.ChargeStateRequiresActionError as e:
            redirect_url = e.redirect_url
            e.charge_state.ledger_item.subscription_charge = subscription_charge
            subscription_charge.last_ledger_item = e.charge_state.ledger_item
            ledger_item = e.charge_state.ledger_item
        else:
            charge_state.ledger_item.subscription_charge = subscription_charge
            subscription_charge.last_ledger_item = charge_state.ledger_item
            ledger_item = charge_state.ledger_item
        subscription_charge.save()
        tasks.try_update_charge_state(ledger_item, False)

        if redirect_url:
            return HttpResponse(json.dumps({
                "id": str(subscription.id),
                "redirect_uri": redirect_url,
            }), content_type='application/json', status=302)
        else:
            return HttpResponse(json.dumps({
                "id": str(subscription.id),
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

            if charge_diff != 0:
                subscription_charge = models.SubscriptionCharge(
                    subscription=subscription,
                    timestamp=now,
                    last_bill_attempted=now,
                    amount=charge_diff,
                )

                redirect_url = None
                try:
                    charge_state = tasks.charge_account(
                        subscription.account, charge_diff, f"{subscription.plan.name} - change in usage",
                        f"sb_{subscription.id}", can_reject=can_reject, off_session=off_session,
                        return_uri=data.get("return_uri"), supports_delayed=True
                    )

                except tasks.ChargeStateRequiresActionError as e:
                    ledger_item = e.charge_state.ledger_item
                    redirect_url = e.redirect_url
                except tasks.ChargeError as e:
                    ledger_item = e.charge_state.ledger_item
                else:
                    ledger_item = charge_state.ledger_item

                subscription_charge.last_ledger_item = ledger_item
                subscription_charge.save()
                ledger_item.subscription_charge = subscription_charge
                ledger_item.save(mail=True, force_mail=True)

                if redirect_url:
                    return HttpResponse(json.dumps({
                        "redirect_uri": redirect_url,
                    }), content_type='application/json', status=302)

        return HttpResponse(status=200)

