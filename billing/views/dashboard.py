import csv

import stripe
import stripe.error
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.template import loader
from django.core.paginator import Paginator
import datetime
import decimal
from django.db.models import Q
from .. import forms, models, tasks
from ..apps import gocardless_client


@login_required
def dashboard(request):
    ledger_items = models.LedgerItem.objects.filter(account=request.user.account)
    active_subscriptions = reversed(sorted(list(request.user.account.subscription_set.filter(
        Q(state=models.Subscription.STATE_ACTIVE) | Q(state=models.Subscription.STATE_PAST_DUE)
    )), key=lambda s: s.next_bill))
    
    ledger_items = Paginator(ledger_items, 10)
    page_number = request.GET.get('page')
    page_obj = ledger_items.get_page(page_number)

    return render(request, "billing/dashboard.html", {
        "ledger_items": page_obj,
        "account": request.user.account,
        "active_subscriptions": active_subscriptions
    })


@login_required
def statement_export(request):
    if request.method == "POST":r
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

    tasks.fail_payment(ledger_item)

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
