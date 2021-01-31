import csv

import stripe
import stripe.error
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.template import loader
import datetime
import decimal
from django.db.models import Q
from .. import forms, models
from ..apps import gocardless_client


@login_required
def dashboard(request):
    ledger_items = models.LedgerItem.objects.filter(account=request.user.account)
    active_subscriptions = reversed(sorted(list(request.user.account.subscription_set.filter(
        Q(state=models.Subscription.STATE_ACTIVE) | Q(state=models.Subscription.STATE_PAST_DUE)
    )), key=lambda s: s.next_bill))

    return render(request, "billing/dashboard.html", {
        "ledger_items": ledger_items,
        "account": request.user.account,
        "active_subscriptions": active_subscriptions
    })


@login_required
def statement_export(request):
    if request.method == "POST":
        form = forms.StatementExportForm(request.POST)
        if form.is_valid():
            from_date = form.cleaned_data["date_from"]
            to_date = form.cleaned_data["date_to"]
            from_datetime = datetime.datetime(
                year=from_date.year, month=from_date.month, day=from_date.day,
                hour=0, minute=0, second=0, microsecond=0, tzinfo=datetime.timezone.utc
            )
            to_datetime = datetime.datetime(
                year=to_date.year, month=to_date.month, day=to_date.day,
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=datetime.timezone.utc
            )
            items = models.LedgerItem.objects.filter(
                account=request.user.account,
                timestamp__gte=from_datetime,
                timestamp__lte=to_datetime,
                state=models.LedgerItem.STATE_COMPLETED
            )
            if form.cleaned_data["format"] == forms.StatementExportForm.FORMAT_CSV:
                response = HttpResponse(content_type='text/csv; charset=utf-8')
                response['Content-Disposition'] = \
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
                starting_balance = request.user.account.balance_at(from_datetime)
                closing_balance = request.user.account.balance_at(to_datetime)

                total_incoming = decimal.Decimal(0)
                total_outgoing = decimal.Decimal(0)

                for item in items:
                    if item.amount >= 0:
                        total_incoming += item.amount
                    else:
                        total_outgoing -= item.amount

                return render(request, "billing/statement_export_pdf.html", {
                    "account": request.user.account,
                    "items": reversed(items),
                    "from_date": from_date,
                    "to_date": to_date,
                    "starting_balance": starting_balance,
                    "closing_balance": closing_balance,
                    "total_incoming": total_incoming,
                    "total_outgoing": total_outgoing,
                })
    else:
        form = forms.StatementExportForm()

    return render(request, "billing/statement_export.html", {
        "form": form
    })

@login_required
def fail_top_up(request, item_id):
    ledger_item = get_object_or_404(models.LedgerItem, id=item_id)

    if ledger_item.account != request.user.account:
        return HttpResponseForbidden

    if ledger_item.state not in (ledger_item.STATE_PENDING, ledger_item.STATE_PROCESSING_CANCELLABLE):
        return redirect('dashboard')

    if ledger_item.type not in (
            ledger_item.TYPE_CARD, ledger_item.TYPE_BACS, ledger_item.TYPE_SOURCES, ledger_item.TYPE_CHECKOUT,
            ledger_item.TYPE_SEPA, ledger_item.TYPE_SOFORT, ledger_item.TYPE_GIROPAY, ledger_item.TYPE_BANCONTACT,
            ledger_item.TYPE_EPS, ledger_item.TYPE_IDEAL, ledger_item.TYPE_P24, ledger_item.TYPE_GOCARDLESS,
            ledger_item.TYPE_STRIPE_BACS
    ):
        return HttpResponseBadRequest()

    if ledger_item.type in (
            ledger_item.TYPE_CARD, ledger_item.TYPE_SEPA, ledger_item.TYPE_SOFORT, ledger_item.TYPE_GIROPAY,
            ledger_item.TYPE_BANCONTACT, ledger_item.TYPE_EPS, ledger_item.TYPE_IDEAL, ledger_item.TYPE_P24,
            ledger_item.TYPE_STRIPE_BACS
    ):
        payment_intent = stripe.PaymentIntent.retrieve(ledger_item.type_id)
        if payment_intent["status"] == "succeeded":
            ledger_item.state = ledger_item.STATE_COMPLETED
            ledger_item.save()
            return redirect('dashboard')
        stripe.PaymentIntent.cancel(ledger_item.type_id)
    elif ledger_item.type == ledger_item.TYPE_CHECKOUT:
        session = stripe.checkout.Session.retrieve(ledger_item.type_id)
        stripe.PaymentIntent.cancel(session["payment_intent"])
    elif ledger_item.type == ledger_item.TYPE_GOCARDLESS:
        gocardless_client.payments.cancel(ledger_item.type_id)

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
