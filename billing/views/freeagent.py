import base64
import decimal
import uuid

import dateutil.parser
import django.contrib.auth
import django_countries
import requests
import stripe
from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.http import HttpResponseBadRequest, HttpResponse
from django.shortcuts import redirect, render, reverse, get_object_or_404
from django.template.loader import render_to_string

from .. import models, tasks, emails


@login_required
@permission_required('billing.change_billingconfig', raise_exception=True)
def link_freeagent(request):
    if not (settings.FREEAGENT_BASE_URL and settings.FREEAGENT_CLIENT_ID):
        return HttpResponseBadRequest()

    return_uri = settings.EXTERNAL_URL_BASE + reverse('link_freeagent_callback')

    return redirect(
        f"{settings.FREEAGENT_BASE_URL}/v2/approve_app?client_id={settings.FREEAGENT_CLIENT_ID}"
        f"&response_type=code&redirect_uri={return_uri}"
    )


@login_required
@permission_required('billing.change_billingconfig', raise_exception=True)
def link_freeagent_callback(request):
    if "code" not in request.GET:
        return HttpResponseBadRequest()

    return_uri = settings.EXTERNAL_URL_BASE + reverse('link_freeagent_callback')

    r = requests.post(f"{settings.FREEAGENT_BASE_URL}/v2/token_endpoint", data={
        "grant_type": "authorization_code",
        "code": request.GET["code"],
        "redirect_uri": return_uri,
        "client_id": settings.FREEAGENT_CLIENT_ID,
        "client_secret": settings.FREEAGENT_CLIENT_SECRET,
    })
    r.raise_for_status()

    models.BillingConfig.load().update_from_freeagent_resp(r.json())
    return redirect('view_accounts')


def freeagent_contact_to_billing_address(contact_data):
    return models.AccountBillingAddress(
        organisation=contact_data.get("organisation_name"),
        street_1=contact_data.get("address1", "N/A"),
        street_2=contact_data.get("address2"),
        street_3=contact_data.get("address3"),
        city=contact_data.get("city", "N/A"),
        province=contact_data.get("region"),
        postal_code=contact_data.get("postcode", "N/A"),
        country_code=django_countries.countries.by_name(contact_data.get("country")),
        vat_id=contact_data.get("sales_tax_registration_number"),
    )


@login_required
@permission_required('billing.add_freeagentinvoice', raise_exception=True)
def send_freeagent_invoice(request):
    config = models.BillingConfig.load()
    freeagent_token = config.get_freeagent_token()

    if not freeagent_token:
        return HttpResponseBadRequest()

    message = None
    if request.method == "POST" and "invoice_url" in request.POST:
        r = requests.get(request.POST.get("invoice_url"), headers={
            "Authorization": f"Bearer {freeagent_token}"
        })
        r.raise_for_status()
        invoice_data = r.json()["invoice"]
        r = requests.get(invoice_data.get("contact"), headers={
            "Authorization": f"Bearer {freeagent_token}"
        })
        r.raise_for_status()
        contact_data = r.json()["contact"]

        invoice_email = contact_data["billing_email"] if "billing_email" in contact_data else \
            contact_data["email"] if "email" in contact_data else None
        if invoice_email:
            r = requests.get(f'{invoice_data.get("url")}/pdf', headers={
                "Authorization": f"Bearer {freeagent_token}"
            })
            r.raise_for_status()
            # pdf_data = base64.b64decode(r.json()["pdf"]["content"])

            account = models.Account.objects.filter(freeagent_contact_id=invoice_data["contact"]).first()
            temp_account = False
            if not account:
                temp_account = True
                UserModel = django.contrib.auth.get_user_model()
                email_field_name = UserModel.get_email_field_name()
                user, _ = UserModel.objects.update_or_create(
                    username=str(uuid.uuid4()),
                    defaults={
                        email_field_name: invoice_email,
                        "first_name": contact_data.get("first_name"),
                        "last_name": contact_data.get("last_name"),
                    },
                )
                account = user.account
                account.freeagent_contact_id = invoice_data["contact"]
                account.save()

            if not account.billing_address:
                billing_address = freeagent_contact_to_billing_address(contact_data)
                billing_address.account = account
                billing_address.save()
                account.billing_address = billing_address
                account.save()

            freeagent_invoice, _ = models.FreeagentInvoice.objects.update_or_create(
                freeagent_id=invoice_data.get("url"),
                defaults={
                    "account": account,
                    "temp_account": temp_account
                }
            )

            bank_details = None
            payment_started = False
            invoice_url = settings.EXTERNAL_URL_BASE + reverse(
                'view_freeagent_invoice', args=(freeagent_invoice.id,)
            )
            net_value = decimal.Decimal(
                invoice_data["net_value"]
            ) * models.ExchangeRate.get_rate(invoice_data["currency"], "GBP")
            due_value = decimal.Decimal(
                invoice_data["due_value"]
            ) * models.ExchangeRate.get_rate(invoice_data["currency"], "GBP")

            if not freeagent_invoice.charge_state:
                try:
                    charge_state = tasks.charge_account(
                        account, net_value, f'Invoice {invoice_data["reference"]}', freeagent_invoice.id,
                        return_uri=invoice_url, supports_delayed=True, force_mail=True
                    )
                    payment_started = True
                except tasks.ChargeStateRequiresActionError as e:
                    charge_state = e.charge_state
                    payment_started = False

                charge_state.ready_to_complete = True
                charge_state.save()
                freeagent_invoice.charge_state = charge_state
                freeagent_invoice.save()

                if not payment_started:
                    amount_int = int(round(due_value * decimal.Decimal(100)))

                    if (
                            account.billing_address.country_code.code.lower() == "gb"
                            or not account.taxable
                    ):
                        ledger_item = models.LedgerItem(
                            account=account,
                            amount=net_value,
                            vat_rate=0,
                            country_code=account.billing_address.country_code.code.lower(),
                            evidence_billing_address=account.billing_address,
                            charged_amount=due_value,
                            eur_exchange_rate=models.ExchangeRate.get_rate("gbp", "eur"),
                            descriptor=f'Bank transfer for invoice {invoice_data["reference"]}',
                            type=models.LedgerItem.TYPE_STRIPE_BACS,
                            state=models.LedgerItem.STATE_PENDING,
                            payment_charge_state=charge_state,
                        )
                        if settings.STRIPE_CLIMATE:
                            ledger_item.stripe_climate_contribution = due_value * decimal.Decimal(
                                settings.STRIPE_CLIMATE_RATE)
                        payment_intent = stripe.PaymentIntent.create(
                            amount=amount_int,
                            currency='gbp',
                            customer=account.get_stripe_id(),
                            description=f'Invoice {invoice_data["reference"]}',
                            receipt_email=account.user.email,
                            payment_method_types=["customer_balance"],
                            payment_method_data={
                                "type": "customer_balance"
                            },
                            payment_method_options={
                                "customer_balance": {
                                    "funding_type": "bank_transfer",
                                    "bank_transfer": {
                                        "types": ["sort_code"]
                                    }
                                }
                            },
                            confirm=True,
                        )
                        ledger_item.type_id = payment_intent['id']
                        ledger_item.save()
                        tasks.update_from_payment_intent(payment_intent, ledger_item)
                        if payment_intent["next_action"]["type"] == "display_bank_transfer_instructions":
                            bank_instructions = payment_intent["next_action"]["display_bank_transfer_instructions"]
                            amount_remaining = decimal.Decimal(
                                bank_instructions["amount_remaining"]
                            ) / decimal.Decimal(100)
                            if bank_instructions["type"] == "sort_code":
                                sort_code = bank_instructions["sort_code"]["sort_code"]
                                bank_details = {
                                    "sort_code": f"{sort_code[0:2]}-{sort_code[2:4]}-{sort_code[4:6]}",
                                    "account_number": bank_instructions["sort_code"]["account_number"],
                                    "type": "gb",
                                    "amount": amount_remaining,
                                    "currency": "GBP",
                                    "reference": bank_instructions["reference"]
                                }

            emails.send_email({
                "subject": f"Your AS207960 / Glauca invoice - {invoice_data['reference']}",
                "content": render_to_string("billing_email/new_invoice.html", {
                    "name": invoice_data['contact_name'],
                    "invoice_url": invoice_url,
                    "bank_details": bank_details,
                    "payment_started": payment_started,
                })
            }, user=account.user)

            r = requests.put(f'{invoice_data.get("url")}/transitions/mark_as_sent', headers={
                "Authorization": f"Bearer {freeagent_token}"
            })
            r.raise_for_status()

            message = "Invoice sent"
        else:
            message = "No email to send invoice to"

    r = requests.get(f"{settings.FREEAGENT_BASE_URL}/v2/invoices", params={
        "view": "draft",
        "per_page": 100
    }, headers={
        "Authorization": f"Bearer {freeagent_token}"
    })
    r.raise_for_status()
    invoices_data = r.json()

    return render(request, "billing/freeagent_invoices.html", {
        "invoices": invoices_data["invoices"],
        "message": message
    })


@login_required
def view_freeagent_invoice(request, invoice_id):
    freeagent_invoice = get_object_or_404(models.FreeagentInvoice, id=invoice_id)

    with transaction.atomic():
        if not freeagent_invoice.account:
            freeagent_invoice.account = request.user.account
            freeagent_invoice.save()
        else:
            if freeagent_invoice.temp_account:
                freeagent_invoice.account = request.user.account.merge_account(freeagent_invoice.account)
                freeagent_invoice.save()
        if freeagent_invoice.charge_state and not freeagent_invoice.charge_state.account:
            freeagent_invoice.charge_state.account = request.user.account
            freeagent_invoice.charge_state.save()

    if freeagent_invoice.account != request.user.account:
        return render(request, "billing/error.html", {
            "error": "You don't have permission to perform this action."
        })

    config = models.BillingConfig.load()
    freeagent_token = config.get_freeagent_token()

    r = requests.get(freeagent_invoice.freeagent_id, headers={
        "Authorization": f"Bearer {freeagent_token}"
    })
    r.raise_for_status()
    invoice_data = r.json()["invoice"]

    can_pay = invoice_data["status"] in ("Open", "Overdue",)

    context_invoice = {
        "id": freeagent_invoice.id,
        "charge_state": freeagent_invoice.charge_state,
        "reference": invoice_data["reference"],
        "status": invoice_data["long_status"],
        "date": dateutil.parser.parse(invoice_data["dated_on"]).date(),
        "due_date": dateutil.parser.parse(invoice_data["due_on"]).date(),
        "net_value": invoice_data["net_value"],
        "total_value": invoice_data["total_value"],
        "due_value": invoice_data["due_value"],
        "currency": invoice_data["currency"],
        "has_sales_tax": invoice_data["involves_sales_tax"],
        "sales_tax_value": invoice_data.get("sales_tax_value"),
        "items": list(map(lambda i: {
            "quantity": i["quantity"],
            "description": i["description"],
            "price": i["price"],
            "tax_rate": i.get("sales_tax_rate"),
            "unit": i.get("item_type"),
            "subtotal": decimal.Decimal(i["price"]) * decimal.Decimal(i["quantity"])
        }, invoice_data["invoice_items"]))
    }

    return render(request, "billing/view_freeagent_invoice.html", {
        "invoice": context_invoice,
        "can_pay": can_pay
    })


@login_required
def view_freeagent_invoice_pdf(request, invoice_id):
    freeagent_invoice = get_object_or_404(models.FreeagentInvoice, id=invoice_id)

    if freeagent_invoice.account != request.user.account:
        return render(request, "billing/error.html", {
            "error": "You don't have permission to perform this action"
        })

    config = models.BillingConfig.load()
    freeagent_token = config.get_freeagent_token()

    r = requests.get(f"{freeagent_invoice.freeagent_id}/pdf", headers={
        "Authorization": f"Bearer {freeagent_token}"
    })
    r.raise_for_status()
    r.raise_for_status()
    pdf_data = base64.b64decode(r.json()["pdf"]["content"])

    return HttpResponse(pdf_data, content_type='application/pdf')
