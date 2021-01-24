import datetime
import decimal
import secrets
import urllib.parse
import inflect
import stripe
import threading
import as207960_utils.models
import django.core.exceptions
from dateutil import relativedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.shortcuts import reverse
from django.core import validators
from django.utils import timezone
from django_countries.fields import CountryField

from . import utils, vat, apps

p = inflect.engine()


class Account(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    exclude_from_accounting = models.BooleanField(blank=True, default=False)
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True)
    default_stripe_payment_method_id = models.CharField(max_length=255, blank=True, null=True)
    default_ach_mandate = models.ForeignKey(
        'ACHMandate', on_delete=models.PROTECT, blank=True, null=True, related_name='default_accounts')
    default_autogiro_mandate = models.ForeignKey(
        'AutogiroMandate', on_delete=models.PROTECT, blank=True, null=True, related_name='default_accounts')
    default_bacs_mandate = models.ForeignKey(
        'BACSMandate', on_delete=models.PROTECT, blank=True, null=True, related_name='default_accounts')
    default_gc_bacs_mandate = models.ForeignKey(
        'GCBACSMandate', on_delete=models.PROTECT, blank=True, null=True, related_name='default_accounts')
    default_becs_mandate = models.ForeignKey(
        'BECSMandate', on_delete=models.PROTECT, blank=True, null=True, related_name='default_accounts')
    default_becs_nz_mandate = models.ForeignKey(
        'BECSNZMandate', on_delete=models.PROTECT, blank=True, null=True, related_name='default_accounts')
    default_betalingsservice_mandate = models.ForeignKey(
        'BetalingsserviceMandate', on_delete=models.PROTECT, blank=True, null=True, related_name='default_accounts')
    default_pad_mandate = models.ForeignKey(
        'PADMandate', on_delete=models.PROTECT, blank=True, null=True, related_name='default_accounts')
    default_sepa_mandate = models.ForeignKey(
        'SEPAMandate', on_delete=models.PROTECT, blank=True, null=True, related_name='default_accounts')
    default_gc_sepa_mandate = models.ForeignKey(
        'GCSEPAMandate', on_delete=models.PROTECT, blank=True, null=True, related_name='default_accounts')
    billing_address = models.ForeignKey(
        'billing.AccountBillingAddress', on_delete=models.PROTECT, blank=True, null=True, related_name='account_current'
    )

    def __str__(self):
        return f"{self.user.first_name} {self.user.last_name} {self.user.email} ({self.user.username})"

    @property
    def balance(self):
        balance = (
            self.ledgeritem_set
           .filter(state=LedgerItem.STATE_COMPLETED)
           .aggregate(balance=models.Sum('amount'))
           .get('balance') or decimal.Decimal(0)
        ).quantize(decimal.Decimal('1.00'))
        return balance if balance != 0 else decimal.Decimal(0)

    @property
    def processing_and_completed_balance(self):
        balance = (
            self.ledgeritem_set
            .filter(Q(state=LedgerItem.STATE_COMPLETED) | Q(state=LedgerItem.STATE_PROCESSING))
            .aggregate(balance=models.Sum('amount'))
            .get('balance') or decimal.Decimal(0)
        ).quantize(decimal.Decimal('1.00'))
        return balance if balance != 0 else decimal.Decimal(0)

    @property
    def pending_balance(self):
        balance = (
            self.ledgeritem_set
            .filter(Q(state=LedgerItem.STATE_PENDING) | Q(state=LedgerItem.STATE_PROCESSING_CANCELLABLE) |
                    Q(state=LedgerItem.STATE_PROCESSING))
            .aggregate(balance=models.Sum('amount'))
            .get('balance') or decimal.Decimal(0)
        ).quantize(decimal.Decimal('1.00'))
        return balance if balance != 0 else decimal.Decimal(0)

    @property
    def reversal_balance(self):
        balance = (
            self.ledgeritem_set
            .filter(is_reversal=True)
            .aggregate(balance=models.Sum('amount'))
            .get('balance') or decimal.Decimal(0)
        ).quantize(decimal.Decimal('1.00'))
        return balance if balance != 0 else decimal.Decimal(0)

    def get_stripe_id(self):
        if self.stripe_customer_id:
            return self.stripe_customer_id

        customer = stripe.Customer.create(
            email=self.user.email,
            description=self.user.username,
            name=f"{self.user.first_name} {self.user.last_name}",
            balance_version='v2',
        )
        customer_id = customer['id']
        self.stripe_customer_id = customer_id
        self.save()
        return customer_id

    def save(self, *args, **kwargs):
        if self.stripe_customer_id:
            t = threading.Thread(target=stripe.Customer.modify, args=(self.stripe_customer_id,), kwargs={
                "email": self.user.email,
                "name": f"{self.user.first_name} {self.user.last_name}",
                "balance_version": "v2"
            })
            t.setDaemon(True)
            t.start()

        super().save(*args, **kwargs)

    @property
    def taxable(self):
        if not self.billing_address:
            return True
        else:
            return not self.billing_address.vat_id

    @property
    def virtual_uk_bank(self):
        try:
            return self.accountstripevirtualukbank
        except django.core.exceptions.ObjectDoesNotExist:
            return None

    @property
    def country(self):
        if not self.billing_address:
            return None
        else:
            return self.billing_address.country_code.code.lower()

    @property
    def can_sell(self):
        if not self.billing_address:
            return False, "We need a billing address for your account."
        if self.billing_address.country_code.code.lower() in vat.DO_NOT_SELL:
            return False, "We can't sell to customers in your country. " \
                          "Please do get in touch if this is something you'd like us to look at changing."
        if self.billing_address.country_code.code.lower() == 'ca':
            postal_code_match = utils.canada_postcode_re.fullmatch(self.billing_address.postal_code)
            if not postal_code_match:
                return False, "We can't sell to you until we have a valid postal code."
            else:
                postal_code_data = postal_code_match.groupdict()
                if postal_code_data["district"] == "S":
                    return False, "We can't sell to customers in Saskatchewan. " \
                                  "Please do get in touch if this is something you'd like us to look at changing."

        return True, None


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Account.objects.create(user=instance)
    instance.account.save()


class AccountStripeVirtualUKBank(models.Model):
    account = models.OneToOneField(Account, on_delete=models.CASCADE)
    sort_code = models.CharField(max_length=6)
    account_number = models.CharField(max_length=11)

    @property
    def formatted_sort_code(self):
        return f"{self.sort_code[0:2]}-{self.sort_code[2:4]}-{self.sort_code[4:6]}"


class AccountBillingAddress(models.Model):
    id = as207960_utils.models.TypedUUIDField('billing_billingaddress', primary_key=True)
    account = models.ForeignKey(Account, on_delete=models.CASCADE, null=True)
    organisation = models.CharField(max_length=255, blank=True, null=True)
    street_1 = models.CharField(max_length=255, verbose_name="Address line 1")
    street_2 = models.CharField(max_length=255, blank=True, null=True, verbose_name="Address line 2")
    street_3 = models.CharField(max_length=255, blank=True, null=True, verbose_name="Address line 3")
    city = models.CharField(max_length=255)
    province = models.CharField(max_length=255, blank=True, null=True)
    postal_code = models.CharField(max_length=255)
    country_code = CountryField(verbose_name="Country")
    vat_id = models.CharField(max_length=255, blank=True, null=True, verbose_name="VAT ID (without country prefix)")
    vat_id_verification_request = models.CharField(max_length=255, blank=True, null=True)
    deleted = models.BooleanField(default=False, blank=True)
    default = models.BooleanField(default=False, blank=True)

    @property
    def formatted(self):
        lines = []
        if self.organisation:
            lines.append(self.organisation)
        lines.append(self.street_1)
        if self.street_2:
            lines.append(self.street_2)
        if self.street_3:
            lines.append(self.street_3)
        lines.append(self.city)
        if self.province:
            lines.append(self.province)
        lines.append(self.postal_code)
        lines.append(f"{self.country_code.name} {self.country_code.unicode_flag}")

        return "\n".join(lines)

    @property
    def formatted_vat_id(self):
        if self.vat_id:
            vat_country_code = vat.get_vies_country_code(self.country_code.code)
            if vat_country_code:
                return f"{vat_country_code} {self.vat_id}"
            else:
                return self.vat_id


class KnownBankAccount(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, null=True)
    country_code = models.CharField(max_length=2, validators=[validators.MinLengthValidator(2)])
    bank_code = models.CharField(max_length=255, blank=True, null=True)
    branch_code = models.CharField(max_length=255, blank=True, null=True)
    account_code = models.CharField(max_length=255)


class KnownStripePaymentMethod(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, null=True)
    method_id = models.CharField(max_length=255)
    country_code = models.CharField(max_length=2, validators=[validators.MinLengthValidator(2)])


class NotificationSubscription(models.Model):
    endpoint = models.TextField()
    key_auth = models.TextField()
    key_p256dh = models.TextField()
    account = models.ForeignKey(Account, on_delete=models.CASCADE)


class StripeMandate(models.Model):
    id = as207960_utils.models.TypedUUIDField('billing_mandate', primary_key=True)
    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True)
    mandate_id = models.CharField(max_length=255)
    payment_method = models.CharField(max_length=255)
    active = models.BooleanField(default=False)

    @classmethod
    def sync_mandate(cls, mandate_id, account):
        mandate_obj = cls.objects.filter(mandate_id=mandate_id).first()
        mandate = stripe.Mandate.retrieve(mandate_id)
        is_active = mandate["status"] == "active"
        if is_active and not (account.default_stripe_payment_method_id or account.default_gc_mandate_id):
            account.default_stripe_payment_method_id = mandate["payment_method"]
            account.save()
        if not mandate_obj:
            if account:
                mandate_obj = cls(
                    mandate_id=mandate_id,
                    active=is_active,
                    payment_method=mandate["payment_method"],
                    account=account
                )
                mandate_obj.save()
        else:
            mandate_obj.active = is_active
            if not is_active and mandate_obj.payment_method == mandate_obj.account.default_stripe_payment_method_id:
                mandate_obj.account.default_stripe_payment_method_id = None
                mandate_obj.account.save()
            mandate_obj.save()

        if is_active:
            payment_method = stripe.PaymentMethod.retrieve(mandate["payment_method"])
            KnownStripePaymentMethod.objects.update_or_create(
                account=account, method_id=payment_method["id"],
                defaults={
                    "country_code": utils.country_from_stripe_payment_method(payment_method)
                }
            )

    class Meta:
        abstract = True


class GCMandate(models.Model):
    id = as207960_utils.models.TypedUUIDField('billing_mandate', primary_key=True)
    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True)
    mandate_id = models.CharField(max_length=255)
    active = models.BooleanField(default=False)

    @classmethod
    def sync_mandate(cls, mandate_id, account):
        mandate_obj = cls.objects.filter(mandate_id=mandate_id).first()
        mandate = apps.gocardless_client.mandates.get(mandate_id)
        is_active = mandate.status in (
            "pending_customer_approval", "pending_submission", "submitted", "active"
        )
        if is_active and not (account.default_stripe_payment_method_id or account.default_gc_mandate_id):
            account.default_gc_mandate_id = mandate.id
            account.save()
        if not mandate_obj:
            if account:
                mandate_obj = cls(
                    mandate_id=mandate.id,
                    active=is_active,
                    account=account
                )
                mandate_obj.save()
        else:
            mandate_obj.active = is_active
            if not is_active and mandate.id == mandate_obj.account.default_gc_mandate_id:
                mandate_obj.account.default_gc_mandate_id = None
                mandate_obj.account.save()
            mandate_obj.save()
        return mandate_obj

    class Meta:
        abstract = True


class ACHMandate(GCMandate):
    class Meta:
        verbose_name = "ACH Mandate"
        verbose_name_plural = "ACH Mandates"


class AutogiroMandate(GCMandate):
    class Meta:
        verbose_name = "Autogiro Mandate"
        verbose_name_plural = "Autogiro Mandates"


class BACSMandate(StripeMandate):
    class Meta:
        verbose_name = "Stripe BACS Mandate"
        verbose_name_plural = "Stripe BACS Mandates"


class GCBACSMandate(GCMandate):
    class Meta:
        verbose_name = "GoCardless BACS Mandate"
        verbose_name_plural = "GoCardless BACS Mandates"


class BECSMandate(GCMandate):
    class Meta:
        verbose_name = "BECS Mandate"
        verbose_name_plural = "BECS Mandates"


class BECSNZMandate(GCMandate):
    class Meta:
        verbose_name = "BECS NZ Mandate"
        verbose_name_plural = "BECS NZ Mandates"


class BetalingsserviceMandate(GCMandate):
    class Meta:
        verbose_name = "Betalingsservice Mandate"
        verbose_name_plural = "Betalingsservice Mandates"


class PADMandate(GCMandate):
    class Meta:
        verbose_name = "PAD Mandate"
        verbose_name_plural = "PAD Mandates"


class SEPAMandate(StripeMandate):
    class Meta:
        verbose_name = "Stripe SEPA Mandate"
        verbose_name_plural = "Stripe SEPA Mandates"


class GCSEPAMandate(GCMandate):
    class Meta:
        verbose_name = "GoCardless SEPA Mandate"
        verbose_name_plural = "GoCardless SEPA Mandates"


class LedgerItem(models.Model):
    STATE_PENDING = "P"
    STATE_PROCESSING_CANCELLABLE = "A"
    STATE_PROCESSING = "S"
    STATE_FAILED = "F"
    STATE_COMPLETED = "C"
    STATES = (
        (STATE_PENDING, "Pending"),
        (STATE_PROCESSING_CANCELLABLE, "Processing (cancellable)"),
        (STATE_PROCESSING, "Processing"),
        (STATE_FAILED, "Failed"),
        (STATE_COMPLETED, "Completed"),
    )

    TYPE_CHARGE = "B"
    TYPE_CARD = "C"
    TYPE_BACS = "F"
    TYPE_SEPA = "E"
    TYPE_SOFORT = "O"
    TYPE_GIROPAY = "G"
    TYPE_BANCONTACT = "N"
    TYPE_EPS = "P"
    TYPE_IDEAL = "I"
    TYPE_P24 = "2"
    TYPE_GOCARDLESS = "D"
    TYPE_SOURCES = "S"
    TYPE_CHARGES = "A"
    TYPE_CHECKOUT = "H"
    TYPE_MANUAL = "M"
    TYPE_STRIPE_REFUND = "R"
    TYPE_STRIPE_BACS = "T"
    TYPES = (
        (TYPE_CHARGE, "Charge"),
        (TYPE_CARD, "Card"),
        (TYPE_BACS, "BACS/Faster payments/SEPA"),
        (TYPE_SEPA, "SEPA Direct Debit"),
        (TYPE_SOFORT, "SOFORT"),
        (TYPE_GIROPAY, "giropay"),
        (TYPE_BANCONTACT, "Bancontact"),
        (TYPE_EPS, "EPS"),
        (TYPE_IDEAL, "iDEAL"),
        (TYPE_P24, "Przelewy24"),
        (TYPE_GOCARDLESS, "GoCardless"),
        (TYPE_SOURCES, "Sources"),
        (TYPE_CHARGES, "Charges"),
        (TYPE_CHECKOUT, "Checkout"),
        (TYPE_MANUAL, "Manual"),
        (TYPE_STRIPE_REFUND, "Stripe refund"),
        (TYPE_STRIPE_BACS, "Stripe bank transfer"),
    )

    id = as207960_utils.models.TypedUUIDField('billing_ledgeritem', primary_key=True)
    account = models.ForeignKey(Account, on_delete=models.CASCADE, null=True)
    descriptor = models.CharField(max_length=255)
    amount = models.DecimalField(decimal_places=2, max_digits=9, default=0)
    timestamp = models.DateTimeField(auto_now_add=True)
    state = models.CharField(max_length=1, choices=STATES, default=STATE_PENDING)
    type = models.CharField(max_length=1, choices=TYPES, default=TYPE_CHARGE)
    type_id = models.CharField(max_length=255, blank=True, null=True)
    is_reversal = models.BooleanField(default=False, blank=True)
    reversal_for = models.ForeignKey(
        'LedgerItem', on_delete=models.PROTECT, blank=True, null=True, related_name='reversals')
    last_state_change_timestamp = models.DateTimeField(blank=True, null=True)
    charged_amount = models.DecimalField(decimal_places=2, max_digits=9, default=0)
    vat_rate = models.DecimalField(decimal_places=2, max_digits=9, default=0)
    country_code = models.CharField(max_length=2, validators=[validators.MinLengthValidator(2)], blank=True, null=True)
    evidence_billing_address = models.ForeignKey(AccountBillingAddress, on_delete=models.PROTECT, blank=True, null=True)
    evidence_bank_account = models.ForeignKey(KnownBankAccount, on_delete=models.PROTECT, blank=True, null=True)
    evidence_stripe_pm = models.ForeignKey(KnownStripePaymentMethod, on_delete=models.PROTECT, blank=True, null=True)
    evidence_ach_mandate = models.ForeignKey(ACHMandate, on_delete=models.PROTECT, blank=True, null=True)
    evidence_autogiro_mandate = models.ForeignKey(AutogiroMandate, on_delete=models.PROTECT, blank=True, null=True)
    evidence_bacs_mandate = models.ForeignKey(BACSMandate, on_delete=models.PROTECT, blank=True, null=True)
    evidence_gc_bacs_mandate = models.ForeignKey(GCBACSMandate, on_delete=models.PROTECT, blank=True, null=True)
    evidence_becs_mandate = models.ForeignKey(BECSMandate, on_delete=models.PROTECT, blank=True, null=True)
    evidence_becs_nz_mandate = models.ForeignKey(BECSNZMandate, on_delete=models.PROTECT, blank=True, null=True)
    evidence_betalingsservice_mandate = models.ForeignKey(
        BetalingsserviceMandate, on_delete=models.PROTECT, blank=True, null=True)
    evidence_pad_mandate = models.ForeignKey(PADMandate, on_delete=models.PROTECT, blank=True, null=True)
    evidence_sepa_mandate = models.ForeignKey(SEPAMandate, on_delete=models.PROTECT, blank=True, null=True)
    evidence_gc_sepa_mandate = models.ForeignKey(GCSEPAMandate, on_delete=models.PROTECT, blank=True, null=True)
    completed_timestamp = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-timestamp']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_state = self.state

    @property
    def original_state(self):
        return self._original_state if not self._state.adding else None

    @original_state.setter
    def original_state(self, val):
        self._original_state = val

    def save(self, mail=True, *args, **kwargs):
        if self.state == self.STATE_COMPLETED and not self.completed_timestamp:
            self.completed_timestamp = timezone.now()
        if self.state != self.original_state:
            self.last_state_change_timestamp = timezone.now()

        super().save(*args, **kwargs)

        from . import tasks
        tasks.try_update_charge_state(instance=self, mail=mail)

    @property
    def type_name(self):
        if self.type == self.TYPE_STRIPE_REFUND:
            return "refund"
        elif self.type == self.TYPE_CHARGE:
            if self.is_reversal:
                return "order refund"
            else:
                return "order"
        else:
            return "payment"

    @property
    def reversal(self):
        return self.reversals.filter(state=self.STATE_COMPLETED).first()

    @property
    def amount_refundable(self):
        if self.state != self.STATE_COMPLETED or self.is_reversal:
            return decimal.Decimal(0)

        account_refundable = self.account.reversal_balance
        if account_refundable <= 0:
            return decimal.Decimal(0)

        if self.type in (
                self.TYPE_GIROPAY, self.TYPE_BANCONTACT, self.TYPE_EPS, self.TYPE_IDEAL, self.TYPE_P24,
                self.TYPE_SOFORT, self.TYPE_CARD, self.TYPE_SEPA, self.TYPE_CHECKOUT
        ):
            if self.type == self.TYPE_CHECKOUT:
                session = stripe.checkout.Session.retrieve(self.type_id)
                payment_intent = stripe.PaymentIntent.retrieve(session["payment_intent"], expand=["payment_method"])
            else:
                payment_intent = stripe.PaymentIntent.retrieve(self.type_id, expand=["payment_method"])
            created_date = datetime.datetime.utcfromtimestamp(payment_intent["created"])
            if created_date + datetime.timedelta(days=180) < datetime.datetime.utcnow():
                return decimal.Decimal(0)

            if payment_intent["payment_method"]["type"] in ("bacs_debit", "sepa_debit"):
                if created_date + datetime.timedelta(days=7) > datetime.datetime.utcnow():
                    return decimal.Decimal(0)

            refunds = stripe.Refund.list(payment_intent=payment_intent["id"])
            total_refunded_local = sum(
                map(
                    lambda r: decimal.Decimal(r["amount"]) / decimal.Decimal(100),
                    filter(lambda r: r["status"] not in ("failed", "canceled"), refunds.auto_paging_iter())
                )
            )

            payment_amount = decimal.Decimal(payment_intent["amount"]) / decimal.Decimal(100)
            exchange_rate = payment_amount / self.charged_amount
            total_refunded = (total_refunded_local / exchange_rate).quantize(decimal.Decimal("1.00"))
            return min(self.charged_amount - total_refunded, account_refundable)
        else:
            return decimal.Decimal(0)

    @property
    def balance_at(self):
        queryset = self.account.ledgeritem_set \
            .filter(timestamp__lte=self.timestamp)

        queryset = queryset.filter(Q(state=self.STATE_COMPLETED) | Q(id=self.id))

        balance = (
            queryset
           .aggregate(balance=models.Sum('amount'))
           .get('balance') or decimal.Decimal(0)
        ).quantize(decimal.Decimal('1.00'))
        return balance if balance != 0 else decimal.Decimal(0)

    def __str__(self):
        return f"{self.descriptor} ({self.id})"


class ChargeState(models.Model):
    id = as207960_utils.models.TypedUUIDField('billing_charge', primary_key=True)
    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True)
    payment_ledger_item = models.OneToOneField(
        LedgerItem, on_delete=models.SET_NULL, blank=True, null=True, related_name='charge_state_payment'
    )
    ledger_item = models.OneToOneField(
        LedgerItem, on_delete=models.PROTECT, related_name='charge_state'
    )
    return_uri = models.URLField(blank=True, null=True)
    notif_queue = models.CharField(max_length=255, blank=True, null=True)
    last_error = models.TextField(blank=True, null=True)
    ready_to_complete = models.BooleanField(default=False, blank=True, null=True)
    can_reject = models.BooleanField(default=True, blank=True, null=True)

    def full_redirect_uri(self):
        if self.return_uri:
            url_parts = list(urllib.parse.urlparse(self.return_uri))
            query = dict(urllib.parse.parse_qsl(url_parts[4]))
            query.update({
                "charge_state_id": self.id
            })
            url_parts[4] = urllib.parse.urlencode(query)
            return urllib.parse.urlunparse(url_parts)
        else:
            return reverse('dashboard')

    def is_complete(self):
        if self.payment_ledger_item:
            if self.payment_ledger_item.state in (
                    LedgerItem.STATE_COMPLETED, LedgerItem.STATE_PROCESSING
            ):
                return True
        else:
            if self.account and self.account.balance >= self.ledger_item.amount:
                return True

        return False


class ExchangeRate(models.Model):
    timestamp = models.DateTimeField()
    currency = models.CharField(max_length=3)
    rate = models.DecimalField(decimal_places=7, max_digits=20)

    def __str__(self):
        return self.currency

    @classmethod
    def get_rate(cls, from_currency, to_currency):
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        if from_currency == to_currency:
            return 1

        from_obj = cls.objects.get(currency=from_currency)
        to_obj = cls.objects.get(currency=to_currency)

        return to_obj.rate / from_obj.rate


class RecurringPlan(models.Model):
    TYPE_RECURRING = "R"
    TYPE_METERED = "M"
    TYPES = (
        (TYPE_RECURRING, "Recurring"),
        (TYPE_METERED, "Metered")
    )

    INTERVAL_DAY = "D"
    INTERVAL_WEEK = "W"
    INTERVAL_MONTH = "M"
    INTERVALS = (
        (INTERVAL_DAY, "Day"),
        (INTERVAL_MONTH, "Month"),
        (INTERVAL_WEEK, "Week")
    )

    TIERS_VOLUME = "V"
    TIERS_GRADUATED = "G"
    TIERS = (
        (TIERS_VOLUME, "Volume"),
        (TIERS_GRADUATED, "Graduated")
    )

    AGGREGATION_MAX = "M"
    AGGREGATION_SUM = "S"
    AGGREGATION_LAST_EVER = "E"
    AGGREGATION_LAST_PERIOD = "P"
    AGGREGATIONS = (
        (AGGREGATION_MAX, "Maximum value over period"),
        (AGGREGATION_SUM, "Sum over period"),
        (AGGREGATION_LAST_EVER, "Last ever value"),
        (AGGREGATION_LAST_PERIOD, "Last value in period"),
    )

    id = as207960_utils.models.TypedUUIDField('billing_recurringplan', primary_key=True)
    name = models.CharField(max_length=255)
    unit_label = models.CharField(max_length=255)
    billing_interval_value = models.PositiveSmallIntegerField()
    billing_interval_unit = models.CharField(max_length=1, choices=INTERVALS)
    billing_type = models.CharField(max_length=1, choices=TYPES)
    tiers_type = models.CharField(max_length=1, choices=TIERS)
    aggregation_type = models.CharField(max_length=1, choices=AGGREGATIONS, blank=True, null=True)
    notif_queue = models.CharField(max_length=255, blank=True, null=True)

    def clean(self):
        if self.billing_type == self.TYPE_RECURRING and self.aggregation_type is not None:
            raise ValidationError("Aggregation type does not apply to recurring types")
        elif self.billing_type == self.TYPE_METERED and self.aggregation_type is None:
            raise ValidationError("Aggregation type is required for metered types")

    @property
    def billing_interval(self):
        if self.billing_interval_unit == self.INTERVAL_DAY:
            return datetime.timedelta(days=self.billing_interval_value)
        elif self.billing_interval_unit == self.INTERVAL_WEEK:
            return datetime.timedelta(weeks=self.billing_interval_value)
        elif self.billing_interval_unit == self.INTERVAL_MONTH:
            return relativedelta.relativedelta(months=self.billing_interval_value)

    def calculate_charge(self, units: int) -> decimal.Decimal:
        if self.tiers_type == self.TIERS_VOLUME:
            tier = self.recurringplantier_set.filter(Q(last_unit__lte=units) | Q(last_unit__isnull=True)) \
                .order_by(F('last_unit').desc(nulls_last=True)).first()
            return (tier.price_per_unit * decimal.Decimal(units)) + tier.flat_fee
        elif self.tiers_type == self.TIERS_GRADUATED:
            tiers = self.recurringplantier_set.order_by(F('last_unit').asc(nulls_last=True))
            total = decimal.Decimal(0)
            for tier in tiers:
                if tier.last_unit:
                    nums = min(units, tier.last_unit)
                else:
                    nums = units
                total += (tier.price_per_unit * decimal.Decimal(nums)) + tier.flat_fee
                units -= nums
                if units <= 0:
                    break
            return total

    def __str__(self):
        return self.name


class RecurringPlanTier(models.Model):
    id = as207960_utils.models.TypedUUIDField('billing_recurringplantier', primary_key=True)
    plan = models.ForeignKey(RecurringPlan, on_delete=models.CASCADE)
    last_unit = models.PositiveIntegerField(blank=True, null=True)
    price_per_unit = models.DecimalField(decimal_places=14, max_digits=28)
    flat_fee = models.DecimalField(decimal_places=2, max_digits=9)

    class Meta:
        ordering = ('last_unit', 'id')


class Subscription(models.Model):
    STATE_PENDING = "E"
    STATE_ACTIVE = "A"
    STATE_PAST_DUE = "P"
    STATE_CANCELLED = "C"
    STATES = (
        (STATE_PENDING, "Pending"),
        (STATE_ACTIVE, "Active"),
        (STATE_PAST_DUE, "Past due"),
        (STATE_CANCELLED, "Cancelled"),
    )

    id = as207960_utils.models.TypedUUIDField('billing_subscription', primary_key=True)
    plan = models.ForeignKey(RecurringPlan, on_delete=models.CASCADE)
    account = models.ForeignKey(Account, on_delete=models.CASCADE, blank=True, null=True)
    last_billed = models.DateTimeField()
    state = models.CharField(max_length=1, choices=STATES)

    @property
    def next_bill(self):
        billing_interval = self.plan.billing_interval
        return self.last_billed + billing_interval

    @property
    def usage_in_period_label(self):
        usage = self.usage_in_period
        return f"{usage} {p.plural(self.plan.unit_label, usage)}"

    @property
    def amount_unpaid(self):
        return (
                self.subscriptioncharge_set
                .filter(ledger_item__state=LedgerItem.STATE_FAILED)
                .aggregate(balance=models.Sum('amount'))
                .get('balance') or decimal.Decimal(0)
        ).quantize(decimal.Decimal('1.00'))

    # @property
    # def last_billed(self):
    #     charge = self.subscriptioncharge_set.first()
    #     if charge:
    #         return charge.timestamp
    #     else:
    #         return None

    @property
    def usage_in_period(self):
        if self.plan.billing_type == RecurringPlan.TYPE_RECURRING:
            last_usage = self.subscriptionusage_set.first()
            if not last_usage:
                return 0
            else:
                return last_usage.usage_units
        elif self.plan.billing_type == RecurringPlan.TYPE_METERED:
            if self.plan.aggregation_type == RecurringPlan.AGGREGATION_LAST_EVER:
                last_usage = self.subscriptionusage_set.first()
                if not last_usage:
                    return 0
                else:
                    return last_usage.usage_units
            elif self.plan.aggregation_type == RecurringPlan.AGGREGATION_LAST_PERIOD:
                last_usage = self.subscriptionusage_set.filter(timestamp__gt=self.last_billed).first()
                if not last_usage:
                    return 0
                else:
                    return last_usage.usage_units
            elif self.plan.aggregation_type == RecurringPlan.AGGREGATION_SUM:
                last_usage = self.subscriptionusage_set.filter(timestamp__gt=self.last_billed) \
                                 .aggregate(usage=models.Sum('usage_units')) \
                                 .get('usage') or 0
                return last_usage
            elif self.plan.aggregation_type == RecurringPlan.AGGREGATION_MAX:
                last_usage = self.subscriptionusage_set.filter(timestamp__gt=self.last_billed) \
                                 .aggregate(usage=models.Max('usage_units')) \
                                 .get('usage') or 0
                return last_usage

    @property
    def next_charge(self):
        return self.plan.calculate_charge(self.usage_in_period)


class SubscriptionUsage(models.Model):
    id = as207960_utils.models.TypedUUIDField('billing_subscriptionusage', primary_key=True)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE)
    timestamp = models.DateTimeField()
    usage_units = models.PositiveIntegerField()

    class Meta:
        ordering = ['-timestamp']


class SubscriptionCharge(models.Model):
    id = as207960_utils.models.TypedUUIDField('billing_subscriptioncharge', primary_key=True)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE)
    timestamp = models.DateTimeField()
    last_bill_attempted = models.DateTimeField()
    failed_bill_attempts = models.PositiveSmallIntegerField(default=0)
    amount = models.DecimalField(decimal_places=2, max_digits=9, default=0)
    ledger_item = models.OneToOneField(LedgerItem, on_delete=models.PROTECT)
    is_setup_charge = models.BooleanField(blank=True, default=False)

    class Meta:
        ordering = ['-timestamp']


def make_invoice_ref():
    return secrets.token_hex(9).upper()


class Invoice(models.Model):
    STATE_DRAFT = "D"
    STATE_OUTSTANDING = "O"
    STATE_PAST_DUE = "P"
    STATE_PAID = "C"
    STATES = (
        (STATE_DRAFT, "Draft"),
        (STATE_OUTSTANDING, "Outstanding"),
        (STATE_PAST_DUE, "Past due"),
        (STATE_PAID, "Paid"),
    )

    id = as207960_utils.models.TypedUUIDField('billing_invoice', primary_key=True)
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    ref = models.CharField(max_length=100, default=make_invoice_ref)
    state = models.CharField(max_length=1, choices=STATES)
    description = models.CharField(max_length=255)
    invoice_date = models.DateTimeField()
    due_date = models.DateTimeField()


class InvoiceFee(models.Model):
    id = as207960_utils.models.TypedUUIDField('billing_invoicefee', primary_key=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE)
    descriptor = models.CharField(max_length=255)
    rate_per_unit = models.DecimalField(decimal_places=14, max_digits=28)
    units = models.DecimalField(decimal_places=14, max_digits=28)


class InvoiceDiscount(models.Model):
    id = as207960_utils.models.TypedUUIDField('billing_invoicediscount', primary_key=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE)
    descriptor = models.CharField(max_length=255)
    amount = models.DecimalField(decimal_places=2, max_digits=9)
