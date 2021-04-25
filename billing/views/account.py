import secrets
import stripe
import stripe.error
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.views.decorators.http import require_POST
from .. import forms, models, tasks
from ..apps import gocardless_client


@login_required
def account_details(request):
    account = request.user.account  # type: models.Account
    cards = []
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
            "is_default": account.default_sepa_mandate == m
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
            "is_default": account.default_bacs_mandate == m
        }

    def map_gc_mandate(m, v, d):
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
            "is_default": d == m
        }

    ach_mandates = list(map(
        lambda m: map_gc_mandate(m, 'view_ach_mandate', account.default_ach_mandate),
        models.ACHMandate.objects.filter(account=account, active=True)
    ))
    autogiro_mandates = list(map(
        lambda m: map_gc_mandate(m, 'view_autogiro_mandate', account.default_autogiro_mandate),
        models.AutogiroMandate.objects.filter(account=account, active=True)
    ))
    bacs_mandates = list(map(map_bacs_mandate, models.BACSMandate.objects.filter(account=account, active=True)))
    bacs_mandates += list(map(
        lambda m: map_gc_mandate(m, 'view_bacs_mandate', account.default_gc_bacs_mandate),
        models.GCBACSMandate.objects.filter(account=account, active=True))
    )
    becs_mandates = list(map(
        lambda m: map_gc_mandate(m, 'view_becs_mandate', account.default_becs_mandate),
        models.BECSMandate.objects.filter(account=account, active=True)
    ))
    becs_nz_mandates = list(map(
        lambda m: map_gc_mandate(m, 'view_becs_nz_mandate', account.default_becs_nz_mandate)
        , models.BECSNZMandate.objects.filter(account=account, active=True)
    ))
    betalingsservice_mandates = list(map(
        lambda m: map_gc_mandate(m, 'view_betalingsservice_mandate', account.default_betalingsservice_mandate),
        models.BetalingsserviceMandate.objects.filter(account=account, active=True)
    ))
    pad_mandates = list(map(
        lambda m: map_gc_mandate(m, 'view_pad_mandate', account.default_pad_mandate),
        models.PADMandate.objects.filter(account=account, active=True)
    ))
    sepa_mandates = list(map(map_sepa_mandate, models.SEPAMandate.objects.filter(account=account, active=True)))
    sepa_mandates += list(map(
        lambda m: map_gc_mandate(m, 'view_sepa_mandate', account.default_gc_sepa_mandate),
        models.GCSEPAMandate.objects.filter(account=account, active=True)
    ))

    subscriptions = account.subscription_set.all()

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
        "billing_address": account.billing_address,
        "known_bank_accounts": known_bank_accounts,
        "taxable": account.taxable,
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
            if (
                    request.user.account.billing_address and
                    request.user.account.billing_address.country_code.code.upper() == payment_method["card"]["country"]
            ) or not request.user.account.taxable:
                request.user.account.default_stripe_payment_method_id = pm_id
                request.user.account.default_ach_mandate = None
                request.user.account.default_autogiro_mandate = None
                request.user.account.default_bacs_mandate = None
                request.user.account.default_gc_bacs_mandate = None
                request.user.account.default_becs_mandate = None
                request.user.account.default_becs_nz_mandate = None
                request.user.account.default_betalingsservice_mandate = None
                request.user.account.default_pad_mandate = None
                request.user.account.default_sepa_mandate = None
                request.user.account.default_gc_sepa_mandate = None
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
    return_uri = request.GET["return_uri"] if "return_uri" in request.GET else reverse('account_details')

    if request.user.account.billing_address:
        return redirect(return_uri)

    if request.method == "POST":
        form = forms.BillingAddressForm(request.POST)
        if form.is_valid():
            form.instance.account = request.user.account
            form.save()

            request.user.account.billing_address = form.instance
            request.user.account.save()

            redirect(return_uri)
    else:
        form = forms.BillingAddressForm()

    return render(request, "billing/billing_address_form.html", {
        "form": form,
        "title": "Add billing address"
    })


@login_required
def edit_billing_address(request, address_id):
    billing_address = get_object_or_404(models.AccountBillingAddress, id=address_id)
    return_uri = request.GET["return_uri"] if "return_uri" in request.GET else reverse('account_details')

    if billing_address.account != request.user.account:
        return HttpResponseForbidden()

    billing_address.id = None

    if request.method == "POST":
        form = forms.BillingAddressForm(request.POST, instance=billing_address)
        if form.is_valid():
            form.save()
            request.user.account.billing_address = form.instance
            request.user.account.save()
            return redirect(return_uri)
    else:
        form = forms.BillingAddressForm(instance=billing_address)

    return render(request, "billing/billing_address_form.html", {
        "form": form,
        "title": "Edit billing address"
    })


def make_mandate_default(mandate):
    gc_mandate = gocardless_client.mandates.get(mandate.mandate_id)
    bank_account = gocardless_client.customer_bank_accounts.get(gc_mandate.links.customer_bank_account)
    if (
            mandate.account.billing_address and
            mandate.account.billing_address.country_code.code.upper() == bank_account.country_code
    ):
        mandate.account.default_gc_mandate_id = mandate.mandate_id
        mandate.account.default_stripe_payment_method_id = None
        mandate.account.save()
    

def make_prefilled_customer(user):
    prefilled_customer = {
        "email": user.email
    }
    if user.account.billing_address.street_1:
        prefilled_customer["address_line1"] = user.account.billing_address.street_1
    if user.account.billing_address.street_2:
        prefilled_customer["address_line2"] = user.account.billing_address.street_2
    if user.account.billing_address.street_3:
        prefilled_customer["address_line3"] = user.account.billing_address.street_3
    if user.account.billing_address.city:
        prefilled_customer["city"] = user.account.billing_address.city
    if user.account.billing_address.province:
        prefilled_customer["region"] = user.account.billing_address.province
    if user.account.billing_address.postal_code:
        prefilled_customer["postal_code"] = user.account.billing_address.postal_code
    if user.account.billing_address.country_code:
        prefilled_customer["country_code"] = user.account.billing_address.country_code.code
    if user.account.billing_address.organisation:
        prefilled_customer["company_name"] = user.account.billing_address.organisation
    else:
        prefilled_customer["given_name"] = user.first_name
        prefilled_customer["family_name"] = user.last_name

    return prefilled_customer


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
def setup_new_ach(request):
    session_id = secrets.token_hex(16)
    request.session["gc_ach_session_id"] = session_id

    if not (
            request.user.account.billing_address and
            request.user.account.billing_address.country_code.code.lower() == "us"
    ):
        return redirect("account_details")

    if "redirect_uri" in request.GET:
        request.session["gc_setup_redirect_uri"] = request.GET["redirect_uri"]

    prefilled_bank_account = {}
    if "dd_account_type" in request.session:
        prefilled_bank_account["account_type"] = request.session.pop("dd_account_type")

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Authorize top-ups to your Glauca account",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('setup_new_ach_complete')),
        "prefilled_customer": make_prefilled_customer(request.user),
        "prefilled_bank_account": prefilled_bank_account,
        "scheme": "ach"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def setup_new_ach_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_ach_session_id")
        }
    )

    m = models.ACHMandate.sync_mandate(redirect_flow.links.mandate, account)
    request.session["selected_payment_method"] = f"ach_mandate_gc;{m.id}"

    redirect_uri = request.session.pop("gc_setup_redirect_uri", reverse('account_details'))
    return redirect(redirect_uri)


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
        gc_mandate = gocardless_client.mandates.get(mandate.mandate_id)
        bank_account = gocardless_client.customer_bank_accounts.get(gc_mandate.links.customer_bank_account)
        if (
                mandate.account.billing_address and
                mandate.account.billing_address.country_code.code.upper() == bank_account.country_code
        ) or not mandate.account.taxable:
            mandate.account.default_stripe_payment_method_id = None
            mandate.account.default_ach_mandate = mandate
            mandate.account.default_autogiro_mandate = None
            mandate.account.default_bacs_mandate = None
            mandate.account.default_gc_bacs_mandate = None
            mandate.account.default_becs_mandate = None
            mandate.account.default_becs_nz_mandate = None
            mandate.account.default_betalingsservice_mandate = None
            mandate.account.default_pad_mandate = None
            mandate.account.default_sepa_mandate = None
            mandate.account.default_gc_sepa_mandate = None
            mandate.account.save()

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
def setup_new_autogiro(request):
    session_id = secrets.token_hex(16)
    request.session["gc_autogiro_session_id"] = session_id

    if not (
            request.user.account.billing_address and
            request.user.account.billing_address.country_code.code.lower() == "se"
    ):
        return redirect("account_details")

    if "redirect_uri" in request.GET:
        request.session["gc_setup_redirect_uri"] = request.GET["redirect_uri"]

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Authorize top-ups to your Glauca account",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('setup_new_autogiro_complete')),
        "prefilled_customer": make_prefilled_customer(request.user),
        "scheme": "autogiro"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def setup_new_autogiro_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_autogiro_session_id")
        }
    )

    m = models.AutogiroMandate.sync_mandate(redirect_flow.links.mandate, account)
    request.session["selected_payment_method"] = f"autogiro_mandate_gc;{m.id}"

    redirect_uri = request.session.pop("gc_setup_redirect_uri", reverse('account_details'))
    return redirect(redirect_uri)


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
        gc_mandate = gocardless_client.mandates.get(mandate.mandate_id)
        bank_account = gocardless_client.customer_bank_accounts.get(gc_mandate.links.customer_bank_account)
        if (
                mandate.account.billing_address and
                mandate.account.billing_address.country_code.code.upper() == bank_account.country_code
        ) or not mandate.account.taxable:
            mandate.account.default_stripe_payment_method_id = None
            mandate.account.default_ach_mandate = None
            mandate.account.default_autogiro_mandate = mandate
            mandate.account.default_bacs_mandate = None
            mandate.account.default_gc_bacs_mandate = None
            mandate.account.default_becs_mandate = None
            mandate.account.default_becs_nz_mandate = None
            mandate.account.default_betalingsservice_mandate = None
            mandate.account.default_pad_mandate = None
            mandate.account.default_sepa_mandate = None
            mandate.account.default_gc_sepa_mandate = None
            mandate.account.save()

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
def setup_new_bacs(request):
    session_id = secrets.token_hex(16)
    request.session["gc_bacs_session_id"] = session_id

    if not (
            request.user.account.billing_address and
            request.user.account.billing_address.country_code.code.lower() == "gb"
    ):
        return redirect("account_details")

    if "redirect_uri" in request.GET:
        request.session["gc_setup_redirect_uri"] = request.GET["redirect_uri"]

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Authorize top-ups to your Glauca account",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('setup_new_bacs_complete')),
        "prefilled_customer": make_prefilled_customer(request.user),
        "scheme": "bacs"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def setup_new_bacs_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_bacs_session_id")
        }
    )

    m = models.GCBACSMandate.sync_mandate(redirect_flow.links.mandate, account)
    request.session["selected_payment_method"] = f"bacs_mandate_gc;{m.id}"

    redirect_uri = request.session.pop("gc_setup_redirect_uri", reverse('account_details'))
    return redirect(redirect_uri)


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
            gc_mandate_obj = gocardless_client.mandates.get(gc_mandate.mandate_id)
            bank_account = gocardless_client.customer_bank_accounts.get(gc_mandate_obj.links.customer_bank_account)
            if (
                    gc_mandate.account.billing_address and
                    gc_mandate.account.billing_address.country_code.code.upper() == bank_account.country_code
            ) or not gc_mandate.account.taxable:
                gc_mandate.account.default_stripe_payment_method_id = None
                gc_mandate.account.default_ach_mandate = None
                gc_mandate.account.default_autogiro_mandate = None
                gc_mandate.account.default_bacs_mandate = None
                gc_mandate.account.default_gc_bacs_mandate = mandate
                gc_mandate.account.default_becs_mandate = None
                gc_mandate.account.default_becs_nz_mandate = None
                gc_mandate.account.default_betalingsservice_mandate = None
                gc_mandate.account.default_pad_mandate = None
                gc_mandate.account.default_sepa_mandate = None
                gc_mandate.account.default_gc_sepa_mandate = None
                gc_mandate.account.save()
    else:
        mandate = get_object_or_404(models.BACSMandate, id=m_id)

        if mandate.account != request.user.account:
            return HttpResponseForbidden()

        if action == "delete":
            stripe.PaymentMethod.detach(mandate.payment_method)
            mandate.active = False
            mandate.save()

        elif action == "default" and mandate.active:
            if (
                mandate.account.billing_address and
                mandate.account.billing_address.country_code.code.lower() == "gb"
            ) or not mandate.account.taxable:
                mandate.account.default_stripe_payment_method_id = None
                mandate.account.default_ach_mandate = None
                mandate.account.default_autogiro_mandate = None
                mandate.account.default_bacs_mandate = mandate
                mandate.account.default_gc_bacs_mandate = None
                mandate.account.default_becs_mandate = None
                mandate.account.default_becs_nz_mandate = None
                mandate.account.default_betalingsservice_mandate = None
                mandate.account.default_pad_mandate = None
                mandate.account.default_sepa_mandate = None
                mandate.account.default_gc_sepa_mandate = None
                mandate.account.save()

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
def setup_new_becs(request):
    session_id = secrets.token_hex(16)
    request.session["gc_becs_session_id"] = session_id

    if not (
            request.user.account.billing_address and
            request.user.account.billing_address.country_code.code.lower() == "au"
    ):
        return redirect("account_details")

    if "redirect_uri" in request.GET:
        request.session["gc_setup_redirect_uri"] = request.GET["redirect_uri"]

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Authorize top-ups to your Glauca account",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('setup_new_becs_complete')),
        "prefilled_customer": make_prefilled_customer(request.user),
        "scheme": "becs"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def setup_new_becs_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_becs_session_id")
        }
    )

    m = models.BECSMandate.sync_mandate(redirect_flow.links.mandate, account)
    request.session["selected_payment_method"] = f"becs_mandate_gc;{m.id}"

    redirect_uri = request.session.pop("gc_setup_redirect_uri", reverse('account_details'))
    return redirect(redirect_uri)


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
        gc_mandate = gocardless_client.mandates.get(mandate.mandate_id)
        bank_account = gocardless_client.customer_bank_accounts.get(gc_mandate.links.customer_bank_account)
        if (
                mandate.account.billing_address and
                mandate.account.billing_address.country_code.code.upper() == bank_account.country_code
        ) or not mandate.account.taxable:
            mandate.account.default_stripe_payment_method_id = None
            mandate.account.default_ach_mandate = None
            mandate.account.default_autogiro_mandate = None
            mandate.account.default_bacs_mandate = None
            mandate.account.default_gc_bacs_mandate = None
            mandate.account.default_becs_mandate = mandate
            mandate.account.default_becs_nz_mandate = None
            mandate.account.default_betalingsservice_mandate = None
            mandate.account.default_pad_mandate = None
            mandate.account.default_sepa_mandate = None
            mandate.account.default_gc_sepa_mandate = None
            mandate.account.save()

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
def setup_new_becs_nz(request):
    session_id = secrets.token_hex(16)
    request.session["gc_becs_nz_session_id"] = session_id

    if not (
            request.user.account.billing_address and
            request.user.account.billing_address.country_code.code.lower() == "nz"
    ):
        return redirect("account_details")

    if "redirect_uri" in request.GET:
        request.session["gc_setup_redirect_uri"] = request.GET["redirect_uri"]

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Authorize top-ups to your Glauca account",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('setup_new_becs_nz_complete')),
        "prefilled_customer": make_prefilled_customer(request.user),
        "scheme": "becs_nz"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def setup_new_becs_nz_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_becs_nz_session_id")
        }
    )

    m = models.BECSNZMandate.sync_mandate(redirect_flow.links.mandate, account)
    request.session["selected_payment_method"] = f"becs_nz_mandate_gc;{m.id}"

    redirect_uri = request.session.pop("gc_setup_redirect_uri", reverse('account_details'))
    return redirect(redirect_uri)


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
        gc_mandate = gocardless_client.mandates.get(mandate.mandate_id)
        bank_account = gocardless_client.customer_bank_accounts.get(gc_mandate.links.customer_bank_account)
        if (
                mandate.account.billing_address and
                mandate.account.billing_address.country_code.code.upper() == bank_account.country_code
        ) or not mandate.account.taxable:
            mandate.account.default_stripe_payment_method_id = None
            mandate.account.default_ach_mandate = None
            mandate.account.default_autogiro_mandate = None
            mandate.account.default_bacs_mandate = None
            mandate.account.default_gc_bacs_mandate = None
            mandate.account.default_becs_mandate = None
            mandate.account.default_becs_nz_mandate = mandate
            mandate.account.default_betalingsservice_mandate = None
            mandate.account.default_pad_mandate = None
            mandate.account.default_sepa_mandate = None
            mandate.account.default_gc_sepa_mandate = None
            mandate.account.save()

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
def setup_new_betalingsservice(request):
    session_id = secrets.token_hex(16)
    request.session["gc_betalingsservice_session_id"] = session_id

    if not (
            request.user.account.billing_address and
            request.user.account.billing_address.country_code.code.lower() == "dk"
    ):
        return redirect("account_details")

    if "redirect_uri" in request.GET:
        request.session["gc_setup_redirect_uri"] = request.GET["redirect_uri"]

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Authorize top-ups to your Glauca account",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('setup_new_betalingsservice_complete')),
        "prefilled_customer": make_prefilled_customer(request.user),
        "scheme": "betalingsservice"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def setup_new_betalingsservice_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_betalingsservice_session_id")
        }
    )

    m = models.BetalingsserviceMandate.sync_mandate(redirect_flow.links.mandate, account)
    request.session["selected_payment_method"] = f"betalingsservice_mandate_gc;{m.id}"

    redirect_uri = request.session.pop("gc_setup_redirect_uri", reverse('account_details'))
    return redirect(redirect_uri)


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
        gc_mandate = gocardless_client.mandates.get(mandate.mandate_id)
        bank_account = gocardless_client.customer_bank_accounts.get(gc_mandate.links.customer_bank_account)
        if (
                mandate.account.billing_address and
                mandate.account.billing_address.country_code.code.upper() == bank_account.country_code
        ) or not mandate.account.taxable:
            mandate.account.default_stripe_payment_method_id = None
            mandate.account.default_ach_mandate = None
            mandate.account.default_autogiro_mandate = None
            mandate.account.default_bacs_mandate = None
            mandate.account.default_gc_bacs_mandate = None
            mandate.account.default_becs_mandate = None
            mandate.account.default_becs_nz_mandate = None
            mandate.account.default_betalingsservice_mandate = mandate
            mandate.account.default_pad_mandate = None
            mandate.account.default_sepa_mandate = None
            mandate.account.default_gc_sepa_mandate = None
            mandate.account.save()

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
def setup_new_pad(request):
    session_id = secrets.token_hex(16)
    request.session["gc_pad_session_id"] = session_id

    if not (
            request.user.account.billing_address and
            request.user.account.billing_address.country_code.code.lower() == "ca"
    ):
        return redirect("account_details")

    if "redirect_uri" in request.GET:
        request.session["gc_setup_redirect_uri"] = request.GET["redirect_uri"]

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Authorize top-ups to your Glauca account",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('setup_new_pad_complete')),
        "prefilled_customer": make_prefilled_customer(request.user),
        "scheme": "pad"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def setup_new_pad_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_pad_session_id")
        }
    )

    m = models.PADMandate.sync_mandate(redirect_flow.links.mandate, account)
    request.session["selected_payment_method"] = f"pad_mandate_gc;{m.id}"

    redirect_uri = request.session.pop("gc_setup_redirect_uri", reverse('account_details'))
    return redirect(redirect_uri)


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
        gc_mandate = gocardless_client.mandates.get(mandate.mandate_id)
        bank_account = gocardless_client.customer_bank_accounts.get(gc_mandate.links.customer_bank_account)
        if (
                mandate.account.billing_address and
                mandate.account.billing_address.country_code.code.upper() == bank_account.country_code
        ) or not mandate.account.taxable:
            mandate.account.default_stripe_payment_method_id = None
            mandate.account.default_ach_mandate = None
            mandate.account.default_autogiro_mandate = None
            mandate.account.default_bacs_mandate = None
            mandate.account.default_gc_bacs_mandate = None
            mandate.account.default_becs_mandate = None
            mandate.account.default_becs_nz_mandate = None
            mandate.account.default_betalingsservice_mandate = None
            mandate.account.default_pad_mandate = mandate
            mandate.account.default_sepa_mandate = None
            mandate.account.default_gc_sepa_mandate = None
            mandate.account.save()

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
def setup_new_sepa(request):
    session_id = secrets.token_hex(16)
    request.session["gc_sepa_session_id"] = session_id

    if "redirect_uri" in request.GET:
        request.session["gc_setup_redirect_uri"] = request.GET["redirect_uri"]

    redirect_flow = gocardless_client.redirect_flows.create(params={
        "description": "Authorize top-ups to your Glauca account",
        "session_token": session_id,
        "success_redirect_url": request.build_absolute_uri(reverse('setup_new_sepa_complete')),
        "prefilled_customer": make_prefilled_customer(request.user),
        "scheme": "sepa_core"
    })

    return redirect(redirect_flow.redirect_url)


@login_required
def setup_new_sepa_complete(request):
    account = request.user.account  # type: models.Account

    redirect_flow = gocardless_client.redirect_flows.complete(
        request.GET.get("redirect_flow_id"),
        params={
            "session_token": request.session.get("gc_sepa_session_id")
        }
    )

    m = models.GCSEPAMandate.sync_mandate(redirect_flow.links.mandate, account)
    request.session["selected_payment_method"] = f"sepa_mandate_gc;{m.id}"

    redirect_uri = request.session.pop("gc_setup_redirect_uri", reverse('account_details'))
    return redirect(redirect_uri)


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
            gc_mandate_obj = gocardless_client.mandates.get(gc_mandate.mandate_id)
            bank_account = gocardless_client.customer_bank_accounts.get(gc_mandate_obj.links.customer_bank_account)
            if (
                    gc_mandate.account.billing_address and
                    gc_mandate.account.billing_address.country_code.code.upper() == bank_account.country_code
            ) or not gc_mandate.account.taxable:
                gc_mandate.account.default_stripe_payment_method_id = None
                gc_mandate.account.default_ach_mandate = None
                gc_mandate.account.default_autogiro_mandate = None
                gc_mandate.account.default_bacs_mandate = None
                gc_mandate.account.default_gc_bacs_mandate = None
                gc_mandate.account.default_becs_mandate = None
                gc_mandate.account.default_becs_nz_mandate = None
                gc_mandate.account.default_betalingsservice_mandate = None
                gc_mandate.account.default_pad_mandate = None
                gc_mandate.account.default_sepa_mandate = None
                gc_mandate.account.default_gc_sepa_mandate = gc_mandate
                gc_mandate.account.save()
    else:
        mandate = get_object_or_404(models.SEPAMandate, id=m_id)

        if mandate.account != request.user.account:
            return HttpResponseForbidden()

        if action == "delete":
            stripe.PaymentMethod.detach(mandate.payment_method)
            mandate.active = False
            mandate.save()

        elif action == "default" and mandate.active:
            stripe_mandate = stripe.Mandate.retrieve(mandate.mandate_id)
            payment_method = stripe.PaymentMethod.retrieve(stripe_mandate["payment_method"])
            if (
                mandate.account.billing_address and
                mandate.account.billing_address.country_code.code.upper() == payment_method["sepa_debit"]["country"]
            ) or not mandate.account.taxable:
                mandate.account.default_stripe_payment_method_id = None
                mandate.account.default_ach_mandate = None
                mandate.account.default_autogiro_mandate = None
                mandate.account.default_bacs_mandate = None
                mandate.account.default_gc_bacs_mandate = None
                mandate.account.default_becs_mandate = None
                mandate.account.default_becs_nz_mandate = None
                mandate.account.default_betalingsservice_mandate = None
                mandate.account.default_pad_mandate = None
                mandate.account.default_sepa_mandate = mandate
                mandate.account.default_gc_sepa_mandate = None
                mandate.account.save()

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
