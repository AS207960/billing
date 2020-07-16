import datetime
import decimal
import uuid
import urllib.parse
import inflect
import stripe
import threading
from dateutil import relativedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.signals import post_save
from django.dispatch import receiver

p = inflect.engine()


class Account(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True)
    default_stripe_payment_method_id = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return str(self.user)

    @property
    def balance(self):
        return (
            self.ledgeritem_set
           .filter(state=LedgerItem.STATE_COMPLETED)
           .aggregate(balance=models.Sum('amount'))
           .get('balance') or decimal.Decimal(0)
        ).quantize(decimal.Decimal('1.00'))

    @property
    def processing_and_completed_balance(self):
        return (
            self.ledgeritem_set
            .filter(Q(state=LedgerItem.STATE_COMPLETED) | Q(state=LedgerItem.STATE_PROCESSING))
            .aggregate(balance=models.Sum('amount'))
            .get('balance') or decimal.Decimal(0)
        ).quantize(decimal.Decimal('1.00'))

    @property
    def pending_balance(self):
        return (
            self.ledgeritem_set
            .filter(Q(state=LedgerItem.STATE_PENDING) | Q(state=LedgerItem.STATE_PROCESSING))
            .aggregate(balance=models.Sum('amount'))
            .get('balance') or decimal.Decimal(0)
        ).quantize(decimal.Decimal('1.00'))

    def get_stripe_id(self):
        if self.stripe_customer_id:
            return self.stripe_customer_id

        customer = stripe.Customer.create(
            email=self.user.email,
            description=self.user.username,
            name=f"{self.user.first_name} {self.user.last_name}"
        )
        customer_id = customer['id']
        self.stripe_customer_id = customer_id
        self.save()
        return customer_id

    def save(self, *args, **kwargs):
        if self.stripe_customer_id:
            t = threading.Thread(target=stripe.Customer.modify, args=(self.stripe_customer_id,), kwargs={
                "email": self.user.email,
                "name": f"{self.user.first_name} {self.user.last_name}"
            })
            t.setDaemon(True)
            t.start()
        super().save(*args, **kwargs)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Account.objects.create(user=instance)
    instance.account.save()


class NotificationSubscription(models.Model):
    endpoint = models.TextField()
    key_auth = models.TextField()
    key_p256dh = models.TextField()
    account = models.ForeignKey(Account, on_delete=models.CASCADE)


class LedgerItem(models.Model):
    STATE_PENDING = "P"
    STATE_PROCESSING = "S"
    STATE_FAILED = "F"
    STATE_COMPLETED = "C"
    STATES = (
        (STATE_PENDING, "Pending"),
        (STATE_PROCESSING, "Processing"),
        (STATE_FAILED, "Failed"),
        (STATE_COMPLETED, "Completed"),
    )

    TYPE_CHARGE = "B"
    TYPE_CARD = "C"
    TYPE_BACS = "F"
    TYPE_SEPA = "E"
    TYPE_SOURCES = "S"
    TYPE_CHARGES = "A"
    TYPE_CHECKOUT = "H"
    TYPE_MANUAL = "M"
    TYPES = (
        (TYPE_CHARGE, "Charge"),
        (TYPE_CARD, "Card"),
        (TYPE_BACS, "BACS/Faster payments/SEPA"),
        (TYPE_SEPA, "SEPA Direct Debit"),
        (TYPE_SOURCES, "Sources"),
        (TYPE_CHARGES, "Charges"),
        (TYPE_CHECKOUT, "Checkout"),
        (TYPE_MANUAL, "Manual"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    account = models.ForeignKey(Account, on_delete=models.CASCADE, null=True)
    descriptor = models.CharField(max_length=255)
    amount = models.DecimalField(decimal_places=2, max_digits=9, default=0)
    timestamp = models.DateTimeField(auto_now_add=True)
    state = models.CharField(max_length=1, choices=STATES, default=STATE_PENDING)
    type = models.CharField(max_length=1, choices=TYPES, default=TYPE_CHARGE)
    type_id = models.CharField(max_length=255, blank=True, null=True)
    is_reversal = models.BooleanField(default=False, blank=True)

    class Meta:
        ordering = ['-timestamp']

    @property
    def balance_at(self):
        queryset = self.account.ledgeritem_set \
            .filter(timestamp__lte=self.timestamp)

        queryset = queryset.filter(Q(state=self.STATE_COMPLETED) | Q(id=self.id))

        return (
            queryset
           .aggregate(balance=models.Sum('amount'))
           .get('balance') or decimal.Decimal(0)
        ).quantize(decimal.Decimal('1.00'))


class Mandate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True)
    mandate_id = models.CharField(max_length=255)
    payment_method = models.CharField(max_length=255)
    active = models.BooleanField(default=False)

    @classmethod
    def sync_mandate(cls, mandate_id, account):
        mandate_obj = cls.objects.filter(mandate_id=mandate_id).first()
        mandate = stripe.Mandate.retrieve(mandate_id)
        is_active = mandate["status"] == "active"
        if is_active and not account.default_stripe_payment_method_id:
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

    class Meta:
        abstract = True


class BACSMandate(Mandate):
    pass


class SEPAMandate(Mandate):
    pass


class ChargeState(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True)
    payment_ledger_item = models.ForeignKey(
        LedgerItem, on_delete=models.SET_NULL, blank=True, null=True, related_name='charge_state_payment_set'
    )
    ledger_item = models.ForeignKey(
        LedgerItem, on_delete=models.SET_NULL, blank=True, null=True, related_name='charge_state_set'
    )
    return_uri = models.URLField(blank=True, null=True)
    last_error = models.TextField(blank=True, null=True)

    def full_redirect_uri(self):
        url_parts = list(urllib.parse.urlparse(self.return_uri))
        query = dict(urllib.parse.parse_qsl(url_parts[4]))
        query.update({
            "charge_state_id": self.id
        })
        url_parts[4] = urllib.parse.urlencode(query)
        return urllib.parse.urlunparse(url_parts)

    def is_complete(self):
        if self.payment_ledger_item:
            if self.payment_ledger_item.state in (
                    LedgerItem.STATE_COMPLETED, LedgerItem.STATE_PROCESSING
            ):
                return True
        elif self.ledger_item:
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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=255)
    unit_label = models.CharField(max_length=255)
    billing_interval_value = models.PositiveSmallIntegerField()
    billing_interval_unit = models.CharField(max_length=1, choices=INTERVALS)
    billing_type = models.CharField(max_length=1, choices=TYPES)
    tiers_type = models.CharField(max_length=1, choices=TIERS)
    aggregation_type = models.CharField(max_length=1, choices=AGGREGATIONS, blank=True, null=True)

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
            tier = self.recurringplantier_set.filter(last_unit__lte=units) \
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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    plan = models.ForeignKey(RecurringPlan, on_delete=models.CASCADE)
    last_unit = models.PositiveIntegerField(blank=True, null=True)
    price_per_unit = models.DecimalField(decimal_places=14, max_digits=28)
    flat_fee = models.DecimalField(decimal_places=2, max_digits=9)

    class Meta:
        ordering = ('last_unit', 'id')


class Subscription(models.Model):
    STATE_ACTIVE = "A"
    STATE_PAST_DUE = "P"
    STATE_CANCELLED = "C"
    STATES = (
        (STATE_ACTIVE, "Active"),
        (STATE_PAST_DUE, "Past due"),
        (STATE_CANCELLED, "Cancelled"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    plan = models.ForeignKey(RecurringPlan, on_delete=models.CASCADE)
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    last_billed = models.DateTimeField()
    last_bill_attempted = models.DateTimeField()
    state = models.CharField(max_length=1, choices=STATES)
    amount_unpaid = models.DecimalField(decimal_places=2, max_digits=9, default="0")

    @property
    def next_bill(self):
        billing_interval = self.plan.billing_interval
        return self.last_billed + billing_interval

    @property
    def usage_in_period_label(self):
        usage = self.usage_in_period
        return f"{usage} {p.plural(self.plan.unit_label, usage)}"

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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE)
    timestamp = models.DateTimeField()
    usage_units = models.PositiveIntegerField()

    class Meta:
        ordering = ['-timestamp']
