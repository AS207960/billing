import decimal

import stripe
import stripe.error
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .. import forms, models, tasks
from ..apps import gocardless_client


@login_required
def account_details(request):
    account = request.user.account  # type: models.Account
    cards = []

    billing_addresses = models.AccountBillingAddress.objects.filter(account=account, deleted=False)
    known_bank_accounts = models.KnownBankAccount.objects.filter(account=account)

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
            "url": reverse(v, args=(m.id,)),
            "is_default": mandate.id == account.default_gc_mandate_id
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
        "error": request.session.pop("error", None),
        "billing_addresses": billing_addresses,
        "known_bank_accounts": known_bank_accounts
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
        "return_uri": request.build_absolute_uri(
            request.GET.get("return_uri") if "return_uri" in request.GET else reverse('account_details')
        )
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
def add_billing_address(request):
    if request.method == "POST":
        form = forms.BillingAddressForm(request.POST)
        if form.is_valid():
            form.instance.account = request.user.account

            default_billing_address = models.AccountBillingAddress.objects \
                .filter(account=request.user.account, deleted=False, default=True).first()
            if not default_billing_address:
                form.instance.default = True

            form.save()
            if "return_uri" in request.GET:
                return redirect(request.GET["return_uri"])
            else:
                return redirect('account_details')
    else:
        form = forms.BillingAddressForm()

    return render(request, "billing/billing_address_form.html", {
        "form": form,
        "title": "Edit billing address"
    })


@login_required
@require_POST
def edit_billing_address(request, address_id):
    billing_address = get_object_or_404(models.AccountBillingAddress, id=address_id)

    if billing_address.account != request.user.account:
        return HttpResponseForbidden()

    action = request.POST.get("action")

    if action == "delete":
        billing_address.deleted = True
        billing_address.default = False
        billing_address.save()

    elif action == "default":
        request.user.account.accountbillingaddress_set.update(default=False)
        billing_address.default = True
        billing_address.save()

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
            mandate.active = False
            mandate.save()

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
            mandate.active = False
            mandate.save()

        elif action == "default" and mandate.active:
            request.user.account.default_stripe_payment_method_id = mandate.payment_method
            request.user.account.default_gc_mandate_id = None
            request.user.account.save()

    return redirect('account_details')


# @login_required
# def edit_subscription(request, s_id):
#     subscription = get_object_or_404(models.Subscription, id=s_id)
#
#     if subscription.account != request.user.account:
#         return HttpResponseForbidden()
#
#     if request.method == "POST":
#         action = request.POST.get("action")
#
#         if action == "retry":
#             try:
#                 charge_state = tasks.charge_account(
#                     subscription.account, subscription.amount_unpaid,
#                     subscription_charge.subscription.plan.name, f"sb_{subscription_charge.subscription.id}",
#                     can_reject=True, off_session=True, supports_delayed=True
#                 )
#             except tasks.ChargeError as e:
#                 subscription_charge.ledger_item = e.charge_state.ledger_item
#             else:
#                 subscription_charge.ledger_item = charge_state.ledger_item
#             subscription_charge.save()
#             try:
#                 tasks.charge_account(
#                     subscription.account, subscription.amount_unpaid, subscription.plan.name, f"sb_{subscription.id}",
#                     off_session=False, return_uri=request.build_absolute_uri(request.get_full_path())
#                 )
#             except tasks.ChargeError as e:
#                 request.session["error"] = e.message
#                 return redirect('account_details')
#             except tasks.ChargeStateRequiresActionError as e:
#                 request.session["charge_state_id"] = str(e.charge_state.id)
#                 return redirect(e.redirect_url)
#
#             subscription.state = subscription.STATE_ACTIVE
#             subscription.last_billed = timezone.now()
#             subscription.amount_unpaid = decimal.Decimal("0")
#             subscription.save()
#     else:
#         if "charge_state_id" in request.GET:
#             charge_state = get_object_or_404(models.ChargeState, id=request.GET.get("charge_state_id"))
#             if not charge_state.is_complete():
#                 request.session["error"] = charge_state.last_error
#                 return redirect('account_details')
#
#     return redirect('account_details')