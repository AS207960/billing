import decimal

import stripe
import datetime
import stripe.error
import pytz
import dateutil.relativedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import DecimalField, OuterRef, Sum, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from .. import forms, models, tasks, vat


@login_required
@permission_required('billing.view_account', raise_exception=True)
def view_accounts(request):
    accounts = models.Account.objects.all()

    balances = models.LedgerItem.objects.filter(account=OuterRef('pk')) \
        .filter(state=models.LedgerItem.STATE_COMPLETED) \
        .order_by().values('account') \
        .annotate(balance=Sum('amount', output_field=DecimalField())) \
        .values('balance')
    total_balance = models.Account.objects \
                        .filter(exclude_from_accounting=False) \
                        .annotate(balance=Subquery(balances, output_field=DecimalField())) \
                        .aggregate(total_balance=Sum('balance')).get('total_balance') or decimal.Decimal(0)

    return render(request, "billing/accounts.html", {
        "accounts": accounts,
        "total_balance": total_balance
    })


@login_required
@permission_required('billing.view_account', raise_exception=True)
def view_account(request, account_id):
    user = get_object_or_404(get_user_model(), username=account_id)
    account = user.account  # type: models.Account
    cards = []

    if account.stripe_customer_id:
        cards = list(stripe.PaymentMethod.list(
            customer=account.stripe_customer_id,
            type="card"
        ).auto_paging_iter())

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
                tasks.charge_account(account, amount, descriptor, type_id, can_reject=can_reject, supports_delayed=True)
            except tasks.ChargeError as e:
                form.errors['__all__'] = (e.message,)
            except tasks.ChargeStateRequiresActionError:
                return redirect('view_account', user.username)
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
                amount = form.cleaned_data['amount']
                gbp_amount = models.ExchangeRate.get_rate(form.cleaned_data['currency'], 'gbp') * amount

                vat_rate = decimal.Decimal(0)
                if ledger_item.account.taxable:
                    country_vat_rate = vat.get_vat_rate(ledger_item.evidence_billing_address.country_code.code.lower())
                    if country_vat_rate is not None:
                        vat_rate = country_vat_rate

                ledger_item.amount = gbp_amount / (1 + vat_rate)
                ledger_item.vat_rate = vat_rate
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


@login_required
@permission_required('billing.view_ledgeritem', raise_exception=True)
def view_account_deferrals(request):
    start_date = datetime.datetime(2020, 1, 1, tzinfo=pytz.timezone("Europe/London"))
    end_date = datetime.datetime.utcnow().astimezone(pytz.timezone("Europe/London"))

    ledger_items = models.LedgerItem.objects.filter(
        state=models.LedgerItem.STATE_COMPLETED,
        account__exclude_from_accounting=False
    )

    reporting_periods = []
    cur_start_date = start_date
    delta = dateutil.relativedelta.relativedelta(months=1)
    while True:
        period_items = ledger_items.filter(
            timestamp__gte=cur_start_date,
            timestamp__lt=cur_start_date + delta
        )
        sales = decimal.Decimal(0)
        prepayments = decimal.Decimal(0)
        for item in period_items:
            if item.amount < 0:
                sales += item.amount
            else:
                prepayments += item.amount

        reporting_periods.append({
            "start_date": cur_start_date.date(),
            "end_date": (cur_start_date + delta).date() - dateutil.relativedelta.relativedelta(days=1),
            "sales": sales,
            "prepayments": prepayments
        })

        cur_start_date += delta
        if cur_start_date >= end_date:
            break

    return render(request, "billing/account_deferrals.html", {
        "reporting_periods": reversed(reporting_periods)
    })
