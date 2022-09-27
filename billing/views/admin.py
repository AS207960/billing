import datetime
import decimal

import dateutil.relativedelta
import django_countries
import pytz
import calendar
import stripe
import stripe.error
import schwifty
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import DecimalField, OuterRef, Sum, Subquery
from django.shortcuts import get_object_or_404, redirect, render, reverse

from .. import forms, models, tasks, vat
from ..views import webhooks


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
    has_freeagent_auth = bool(models.BillingConfig.load().get_freeagent_token())

    return render(request, "billing/accounts.html", {
        "accounts": accounts,
        "total_balance": total_balance,
        "has_freeagent_auth":  has_freeagent_auth
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
    known_bank_accounts = models.KnownBankAccount.objects.filter(account=account)

    return render(request, "billing/account.html", {
        "account": account,
        "cards": cards,
        "bacs_mandates": bacs_mandates,
        "sepa_mandates": sepa_mandates,
        "known_bank_accounts": known_bank_accounts,
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


def form_to_account_data(form):
    trans_account_data = None

    if form.cleaned_data['bank_country']:
        trans_account_data = {
            "country_code": form.cleaned_data['bank_country'].lower(),
            "bank_code": form.cleaned_data['bank_code'] if form.cleaned_data['bank_code'] else None,
            "branch_code": form.cleaned_data['branch_code'] if form.cleaned_data['branch_code'] else None,
            "account_code": form.cleaned_data['account_number']
        }
    else:
        try:
            trans_iban = schwifty.IBAN(form.cleaned_data['account_number'])
        except ValueError:
            form.add_error('account_number', "Invalid IBAN")
        else:
            trans_account_data = {
                "country_code": trans_iban.country_code.lower(),
                "bank_code": trans_iban.bank_code,
                "branch_code": trans_iban.branch_code,
                "account_code": trans_iban.account_code
            }

    return trans_account_data


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

                trans_account_data = form_to_account_data(form)

                error = webhooks.attempt_complete_bank_transfer(
                    ref=None, amount=gbp_amount, trans_account_data=trans_account_data,
                    data=None, ledger_item=ledger_item, known_account=None
                )
                if error:
                    form.add_error(None, error)

                return redirect('view_account', ledger_item.account.user.username)
        else:
            form = forms.BACSMarkPaidForm()

        return render(request, "billing/account_bacs_mark_paid.html", {
            "form": form,
            "legder_item": ledger_item,
        })

    return redirect('view_account', ledger_item.account.user.username)


@login_required
@permission_required('billing.add_knownbankaccount', raise_exception=True)
def add_bank_account(request, account_id):
    user = get_object_or_404(get_user_model(), username=account_id)
    account = user.account  # type: models.Account

    if request.method == "POST":
        form = forms.GenericBankAccountForm(request.POST)
        if form.is_valid():
            trans_account_data = form_to_account_data(form)
            if trans_account_data:
                _known_account, _ = models.KnownBankAccount.objects.update_or_create(
                    account=account,
                    **trans_account_data
                )
                return redirect('view_account', user.username)
    else:
        form = forms.GenericBankAccountForm()

    return render(request, "billing/add_bank_account.html", {
        "account": account,
        "form": form
    })


@login_required
@permission_required('billing.view_ledgeritem', raise_exception=True)
def view_account_deferrals(request):
    start_date = datetime.datetime(2020, 1, 1, tzinfo=pytz.timezone("Europe/London"))
    end_date = datetime.datetime.utcnow().astimezone(pytz.timezone("Europe/London"))

    ledger_items = models.LedgerItem.objects.filter(
        state=models.LedgerItem.STATE_COMPLETED,
        account__exclude_from_accounting=False
    ).filter(
        ~Q(type=models.LedgerItem.TYPE_MANUAL)
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


@login_required
@permission_required('billing.view_ledgeritem', raise_exception=True)
def view_vat_gb(request):
    quarters = (
        ((1, 1), (3, 31)),
        ((4, 1), (6, 30)),
        ((7, 1), (9, 30)),
        ((10, 1), (12, 31)),
    )

    if request.method == "POST":
        form = forms.VATMOSSForm(request.POST)

        if form.is_valid():
            quarter_dates = quarters[form.cleaned_data['quarter'] - 1]
            quarter_start_date = datetime.date(
                year=form.cleaned_data['year'], month=quarter_dates[0][0], day=quarter_dates[0][1])
            quarter_end_date = datetime.date(
                year=form.cleaned_data['year'], month=quarter_dates[1][0], day=quarter_dates[1][1])
            quarter_start_datetime = datetime.datetime(
                year=quarter_start_date.year, month=quarter_start_date.month, day=quarter_start_date.day,
                hour=0, minute=0, second=0, microsecond=0, tzinfo=datetime.timezone.utc
            )
            quarter_end_datetime = datetime.datetime(
                year=quarter_end_date.year, month=quarter_end_date.month, day=quarter_end_date.day,
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=datetime.timezone.utc
            )
            items = models.LedgerItem.objects.filter(
                timestamp__gte=quarter_start_datetime,
                timestamp__lte=quarter_end_datetime,
                state=models.LedgerItem.STATE_COMPLETED,
                type=models.LedgerItem.TYPE_CHARGE,
                country_code__in=("gb", "im"),
                account__exclude_from_accounting=False
            )
            months = {}
            for item in items:
                if item.timestamp.month not in months:
                    months[item.timestamp.month] = {}
                vat_rate = str(item.vat_rate)
                if vat_rate not in months[item.timestamp.month]:
                    months[item.timestamp.month][vat_rate] = decimal.Decimal(0)
                months[item.timestamp.month][vat_rate] += -item.amount

            def map_vat_rate(v):
                rate = decimal.Decimal(v[0])
                return {
                    "vat_rate": rate * decimal.Decimal(100),
                    "total_sales_gbp": v[1],
                    "vat_due_gbp": v[1] * rate,
                }

            def map_vat_month(m):
                vat_rates = list(map(map_vat_rate, m[1].items()))
                month_vat_gbp = sum(map(lambda v: v["vat_due_gbp"], vat_rates))
                return {
                    "vat_rates": vat_rates,
                    "vat_due_gbp": month_vat_gbp,
                    "month_name": calendar.month_name[m[0]]
                }

            vat_months = list(map(map_vat_month, months.items()))
            total_vat_gbp = sum(map(lambda c: c["vat_due_gbp"], vat_months))
            return render(request, "billing/vat_gb_export.html", {
                "export_year": form.cleaned_data['year'],
                "export_quarter": form.cleaned_data['quarter'],
                "vat_months": vat_months,
                "total_vat_gbp": total_vat_gbp,
            })
    else:
        form = forms.VATMOSSForm()

    return render(request, "billing/vat_gb_select.html", {
        "form": form
    })


@login_required
@permission_required('billing.view_ledgeritem', raise_exception=True)
def view_vat_tr(request):

    if request.method == "POST":
        form = forms.VATTRForm(request.POST)

        if form.is_valid():
            month_start_date = datetime.date(
                year=form.cleaned_data['year'], month=form.cleaned_data['month'], day=1)
            month_end_date = datetime.date(
                year=form.cleaned_data['year'] + (1 if form.cleaned_data['month'] == 12 else 0),
                month=(form.cleaned_data['month'] % 12) + 1, day=1
            ) - datetime.timedelta(days=1)
            print(month_start_date, month_end_date)
            month_start_datetime = datetime.datetime(
                year=month_start_date.year, month=month_start_date.month, day=month_start_date.day,
                hour=0, minute=0, second=0, microsecond=0, tzinfo=datetime.timezone.utc
            )
            month_end_datetime = datetime.datetime(
                year=month_end_date.year, month=month_end_date.month, day=month_end_date.day,
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=datetime.timezone.utc
            )
            items = models.LedgerItem.objects.filter(
                timestamp__gte=month_start_datetime,
                timestamp__lte=month_end_datetime,
                state=models.LedgerItem.STATE_COMPLETED,
                type=models.LedgerItem.TYPE_CHARGE,
                country_code="tr",
                account__exclude_from_accounting=False
            )
            vat_rates = {}
            for item in items:
                vat_rate = str(item.vat_rate)
                if vat_rate not in vat_rates:
                    vat_rates[vat_rate] = {
                        "gbp": decimal.Decimal(0),
                        "try": decimal.Decimal(0),
                    }

                if item.try_exchange_rate:
                    exchange_rate = item.try_exchange_rate
                elif item.reversal_for and item.reversal_for.try_exchange_rate:
                    exchange_rate = item.reversal_for.try_exchange_rate
                else:
                    exchange_rate = models.ExchangeRate.get_rate("gbp", "try")
                vat_rates[vat_rate]["gbp"] += -item.amount
                vat_rates[vat_rate]["try"] += -item.amount * exchange_rate

            def map_vat_rate(v):
                rate = decimal.Decimal(v[0])
                return {
                    "vat_rate": rate * decimal.Decimal(100),
                    "total_sales_gbp": v[1]["gbp"],
                    "total_sales_try": v[1]["try"],
                    "vat_due_gbp": v[1]["gbp"] * rate,
                    "vat_due_try": v[1]["try"] * rate,
                }

            vat_rates = list(map(map_vat_rate, vat_rates.items()))
            month_vat_gbp = sum(map(lambda v: v["vat_due_gbp"], vat_rates))
            month_vat_try = sum(map(lambda v: v["vat_due_try"], vat_rates))
            return render(request, "billing/vat_tr_export.html", {
                "export_year": form.cleaned_data['year'],
                "export_month": form.cleaned_data['month'],
                "vat_rates": vat_rates,
                "total_vat_gbp": month_vat_gbp,
                "total_vat_try": month_vat_try,
            })
    else:
        form = forms.VATTRForm()

    return render(request, "billing/vat_tr_select.html", {
        "form": form
    })


@login_required
@permission_required('billing.view_ledgeritem', raise_exception=True)
def view_vat_moss(request):
    quarters = (
        ((1, 1), (3, 31)),
        ((4, 1), (6, 30)),
        ((7, 1), (9, 30)),
        ((10, 1), (12, 31)),
    )

    if request.method == "POST":
        form = forms.VATMOSSForm(request.POST)

        if form.is_valid():
            quarter_dates = quarters[form.cleaned_data['quarter'] - 1]
            quarter_start_date = datetime.date(
                year=form.cleaned_data['year'], month=quarter_dates[0][0], day=quarter_dates[0][1])
            quarter_end_date = datetime.date(
                year=form.cleaned_data['year'], month=quarter_dates[1][0], day=quarter_dates[1][1])
            quarter_start_datetime = datetime.datetime(
                year=quarter_start_date.year, month=quarter_start_date.month, day=quarter_start_date.day,
                hour=0, minute=0, second=0, microsecond=0, tzinfo=datetime.timezone.utc
            )
            quarter_end_datetime = datetime.datetime(
                year=quarter_end_date.year, month=quarter_end_date.month, day=quarter_end_date.day,
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=datetime.timezone.utc
            )
            items = models.LedgerItem.objects.filter(
                timestamp__gte=quarter_start_datetime,
                timestamp__lte=quarter_end_datetime,
                state=models.LedgerItem.STATE_COMPLETED,
                type=models.LedgerItem.TYPE_CHARGE,
                country_code__in=vat.VAT_MOSS_COUNTRIES,
                account__exclude_from_accounting=False
            )
            countries = {}
            for item in items:
                if item.country_code not in countries:
                    countries[item.country_code] = {}
                if item.timestamp.month not in countries[item.country_code]:
                    countries[item.country_code][item.timestamp.month] = {}
                vat_rate = str(item.vat_rate)
                if vat_rate not in countries[item.country_code][item.timestamp.month]:
                    countries[item.country_code][item.timestamp.month][vat_rate] = {
                        "gbp": decimal.Decimal(0),
                        "eur": decimal.Decimal(0),
                    }
                if item.eur_exchange_rate:
                    exchange_rate = item.eur_exchange_rate
                elif item.reversal_for and item.reversal_for.eur_exchange_rate:
                    exchange_rate = item.reversal_for.eur_exchange_rate
                else:
                    exchange_rate = models.ExchangeRate.get_rate("gbp", "eur")
                countries[item.country_code][item.timestamp.month][vat_rate]["gbp"] += -item.amount
                countries[item.country_code][item.timestamp.month][vat_rate]["eur"]\
                    += -item.amount * exchange_rate

            def map_vat_rate(v):
                rate = decimal.Decimal(v[0])
                return {
                    "vat_rate": rate * decimal.Decimal(100),
                    "total_sales_gbp": v[1]["gbp"],
                    "total_sales_eur": v[1]["eur"],
                    "vat_due_gbp": v[1]["gbp"] * rate,
                    "vat_due_eur": v[1]["eur"] * rate,
                }

            def map_vat_month(m):
                vat_rates = list(map(map_vat_rate, m[1].items()))
                month_vat_gbp = sum(map(lambda v: v["vat_due_gbp"], vat_rates))
                month_vat_eur = sum(map(lambda v: v["vat_due_eur"], vat_rates))
                return {
                    "vat_rates": vat_rates,
                    "vat_due_gbp": month_vat_gbp,
                    "vat_due_eur": month_vat_eur,
                    "month_name": calendar.month_name[m[0]]
                }

            def map_country(c):
                cc = c[0].upper()
                vat_months = list(map(map_vat_month, c[1].items()))
                country_vat_gbp = sum(map(lambda v: v["vat_due_gbp"], vat_months))
                country_vat_eur = sum(map(lambda v: v["vat_due_eur"], vat_months))

                vat_rates = {}
                for vat_month in vat_months:
                    for vat_rate in vat_month["vat_rates"]:
                        vat_rate_per = vat_rate["vat_rate"] / decimal.Decimal(100)
                        if vat_rate_per not in vat_rates:
                            vat_rates[vat_rate_per] = {
                                "gbp": decimal.Decimal(0),
                                "eur": decimal.Decimal(0),
                            }
                        vat_rates[vat_rate_per]["gbp"] += vat_rate["total_sales_gbp"]
                        vat_rates[vat_rate_per]["eur"] += vat_rate["total_sales_eur"]

                return {
                    "country_code": c[0],
                    "country_name": dict(django_countries.countries)[cc],
                    "country_emoji": chr(ord(cc[0]) + 127397) + chr(ord(cc[1]) + 127397),
                    "vat_months": vat_months,
                    "vat_rate_sums": list(map(map_vat_rate, vat_rates.items())),
                    "total_vat_gbp": country_vat_gbp,
                    "total_vat_eur": country_vat_eur,
                }

            countries = list(map(map_country, countries.items()))
            total_vat_eur = sum(map(lambda c: c["total_vat_eur"], countries))
            total_vat_gbp = sum(map(lambda c: c["total_vat_gbp"], countries))
            return render(request, "billing/vat_moss_export.html", {
                "export_year": form.cleaned_data['year'],
                "export_quarter": form.cleaned_data['quarter'],
                "countries": countries,
                "total_vat_eur": total_vat_eur,
                "total_vat_gbp": total_vat_gbp,
            })
    else:
        form = forms.VATMOSSForm()

    return render(request, "billing/vat_moss_select.html", {
        "form": form
    })
