from django.db import models
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
import stripe
import decimal
import uuid


class Account(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True)
    default_stripe_payment_method_id = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return str(self.user)

    @property
    def balance(self):
        return self.ledgeritem_set\
            .filter(state=LedgerItem.STATE_COMPLETED)\
            .aggregate(balance=models.Sum('amount'))\
            .get('balance') or decimal.Decimal(0)

    @property
    def pending_balance(self):
        return self.ledgeritem_set\
            .filter(state=LedgerItem.STATE_PENDING)\
            .aggregate(balance=models.Sum('amount'))\
            .get('balance') or decimal.Decimal(0)

    def get_stripe_id(self):
        if self.stripe_customer_id:
            return self.stripe_customer_id

        customer = stripe.Customer.create()
        customer_id = customer['id']
        self.stripe_customer_id = customer_id
        self.save()
        return customer_id


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Account.objects.create(user=instance)
    instance.account.save()


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
    TYPES = (
        (TYPE_CHARGE, "Charge"),
        (TYPE_CARD, "Card"),
        (TYPE_BACS, "BACS/Faster payments/SEPA"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    descriptor = models.CharField(max_length=255)
    amount = models.DecimalField(decimal_places=2, max_digits=9, default=0)
    timestamp = models.DateTimeField(auto_now_add=True)
    state = models.CharField(max_length=1, choices=STATES, default=STATE_PENDING)
    type = models.CharField(max_length=1, choices=TYPES, default=TYPE_CHARGE)
    type_id = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        ordering = ['-timestamp']

    @property
    def balance_at(self):
        queryset = self.account.ledgeritem_set \
            .filter(timestamp__lte=self.timestamp)

        if self.state != self.STATE_PENDING:
            queryset = queryset.filter(state=self.STATE_COMPLETED)
        else:
            queryset = queryset.filter(state__in=(self.STATE_COMPLETED, self.STATE_PENDING))

        return queryset \
            .aggregate(balance=models.Sum('amount')) \
            .get('balance') or decimal.Decimal(0)
