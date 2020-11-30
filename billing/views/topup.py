import decimal
import secrets

import stripe
import stripe.error
import gocardless_pro.errors
import schwifty
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, HttpResponseForbidden, Http404
from django.shortcuts import get_object_or_404, redirect, render, reverse
from .. import forms, models, utils, tasks
from ..apps import gocardless_client


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
        amount_int = int(round(amount_currency * decimal.Decimal(100)))
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
        amount_int = int(round(amount_currency * decimal.Decimal(100)))
        payment_method = stripe.PaymentMethod.retrieve(card_id)

        if payment_method['customer'] != request.user.account.stripe_customer_id:
            return HttpResponseForbidden()

        redirect_uri = reverse('dashboard')

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
        tasks.update_from_payment_intent(payment_intent, ledger_item)

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
    amount_int = int(round(amount_usd * decimal.Decimal(100)))

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
    amount_int = int(round(amount_sek * decimal.Decimal(100)))

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
    amount_int = int(round(amount * decimal.Decimal(100)))

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
    amount_int = int(round(amount_aud * decimal.Decimal(100)))

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
    amount_int = int(round(amount_nzd * decimal.Decimal(100)))

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
    amount_int = int(round(amount_dkk * decimal.Decimal(100)))

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
    amount_int = int(round(amount_cad * decimal.Decimal(100)))

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
    amount_int = int(round(amount_eur * decimal.Decimal(100)))

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
    amount_int = int(round(amount * decimal.Decimal(100)))

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
    amount_int = int(round(amount_usd * decimal.Decimal(100)))

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
    amount_int = int(round(amount_sek * decimal.Decimal(100)))

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
    amount_int = int(round(amount_aud * decimal.Decimal(100)))

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
    amount_int = int(round(amount_nzd * decimal.Decimal(100)))

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
    amount_int = int(round(amount_dkk * decimal.Decimal(100)))

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
    amount_int = int(round(amount_cad * decimal.Decimal(100)))

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
    amount_int = int(round(amount_eur * decimal.Decimal(100)))

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

    if request.method == "POST":
        form = forms.SOFORTForm(request.POST)
        if form.is_valid():
            if "amount" not in request.session:
                return redirect("top_up")
            amount = decimal.Decimal(request.session.pop("amount"))

            redirect_uri = reverse('dashboard')

            amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
            amount_int = int(round(amount_eur * decimal.Decimal(100)))
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
                            "ip_address": str(utils.get_ip(request)),
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
            tasks.update_from_payment_intent(payment_intent, ledger_item)

            if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
                return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])

            return redirect(redirect_uri)

    return render(request, "billing/top_up_sofort.html")


@login_required
def top_up_giropay(request):
    account = request.user.account

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
    amount_int = int(round(amount_eur * decimal.Decimal(100)))

    redirect_uri = reverse('dashboard')

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
    tasks.update_from_payment_intent(payment_intent, ledger_item)

    if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
        return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])

    return redirect(redirect_uri)


@login_required
def top_up_bancontact(request):
    account = request.user.account

    if request.method == "POST" and request.POST.get("accept") == "true":
        if "amount" not in request.session:
            return redirect("top_up")
        amount = decimal.Decimal(request.session.pop("amount"))
        amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
        amount_int = int(round(amount_eur * decimal.Decimal(100)))

        redirect_uri = reverse('dashboard')

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
                        "ip_address": str(utils.get_ip(request)),
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
        tasks.update_from_payment_intent(payment_intent, ledger_item)

        if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
            return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])

        return redirect(redirect_uri)

    return render(request, "billing/top_up_mandate.html", {
        "scheme": "Bancontact"
    })


@login_required
def top_up_eps(request):
    account = request.user.account

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
    amount_int = int(round(amount_eur * decimal.Decimal(100)))

    redirect_uri = reverse('dashboard')

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
    tasks.update_from_payment_intent(payment_intent, ledger_item)

    if payment_intent.get("next_action") and payment_intent["next_action"]["type"] == "redirect_to_url":
        return redirect(payment_intent["next_action"]["redirect_to_url"]["url"])

    return redirect(redirect_uri)


@login_required
def top_up_ideal(request):
    account = request.user.account

    if request.method == "POST" and request.POST.get("accept") == "true":
        if "amount" not in request.session:
            return redirect("top_up")
        amount = decimal.Decimal(request.session.pop("amount"))
        amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
        amount_int = int(round(amount_eur * decimal.Decimal(100)))

        redirect_uri = reverse('dashboard')

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
                        "ip_address": str(utils.get_ip(request)),
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
        tasks.update_from_payment_intent(payment_intent, ledger_item)

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
    amount_int = int(round(amount_eur * decimal.Decimal(100)))
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
    tasks.update_from_source(source, ledger_item)

    return redirect(source["redirect"]["url"])


@login_required
def top_up_p24(request):
    account = request.user.account

    if "amount" not in request.session:
        return redirect("top_up")
    amount = decimal.Decimal(request.session.pop("amount"))
    amount_eur = models.ExchangeRate.get_rate('gbp', 'eur') * amount
    amount_int = int(round(amount_eur * decimal.Decimal(100)))

    redirect_uri = reverse('dashboard')

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
    tasks.update_from_payment_intent(payment_intent, ledger_item)

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
