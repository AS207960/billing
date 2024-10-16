import abc
import dataclasses
import datetime
import decimal
import secrets
import string
import typing
import urllib.parse
import inflect
import stripe
import threading
import requests
import as207960_utils.models
import django.core.exceptions
from dateutil import relativedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.shortcuts import reverse
from django.core import validators
from django.utils import timezone
from django_countries.fields import CountryField

from . import utils, vat, apps

p = inflect.engine()


class BillingConfig(models.Model):
    freeagent_access_token = models.TextField(blank=True, null=True)
    freeagent_refresh_token = models.TextField(blank=True, null=True)
    freeagent_access_token_expires_at = models.DateTimeField(blank=True, null=True)
    freeagent_refresh_token_expires_at = models.DateTimeField(blank=True, null=True)

    def save(self, *args, **kwargs):
        self.__class__.objects.exclude(id=self.id).delete()
        super().save(*args, **kwargs)

    def delete(self, *_args, **_kwargs):
        pass

    @classmethod
    def load(cls):
        try:
            return cls.objects.get()
        except cls.DoesNotExist:
            return cls()

    def update_from_freeagent_resp(self, data):
        now = timezone.now()
        self.freeagent_access_token = data.get("access_token")
        self.freeagent_refresh_token = data.get("refresh_token")
        access_token_expires_in = data.get("expires_in")
        refresh_token_expires_in = data.get("refresh_token_expires_in")
        if access_token_expires_in:
            self.freeagent_access_token_expires_at = now + datetime.timedelta(seconds=access_token_expires_in)
        if refresh_token_expires_in:
            self.freeagent_refresh_token_expires_at = now + datetime.timedelta(seconds=refresh_token_expires_in)
        self.save()

    def get_freeagent_token(self):
        now = timezone.now()
        if self.freeagent_access_token:
            if (not self.freeagent_access_token_expires_at) or self.freeagent_access_token_expires_at > now:
                return self.freeagent_access_token

        if self.freeagent_refresh_token:
            if (not self.freeagent_refresh_token_expires_at) or self.freeagent_refresh_token_expires_at > now:
                r = requests.post(f"{settings.FREEAGENT_BASE_URL}/v2/token_endpoint", data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.freeagent_refresh_token,
                    "client_id": settings.FREEAGENT_CLIENT_ID,
                    "client_secret": settings.FREEAGENT_CLIENT_SECRET,
                })
                if r.status_code >= 400:
                    return None
                r.raise_for_status()
                data = r.json()
                self.update_from_freeagent_resp(data)
                return self.freeagent_access_token

        return None


class Account(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    exclude_from_accounting = models.BooleanField(blank=True, default=False)
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True)
    gocardless_customer_id = models.CharField(max_length=255, blank=True, null=True)
    freeagent_contact_id = models.CharField(max_length=255, blank=True, null=True)
    cloudflare_account_id = models.CharField(max_length=255, blank=True, null=True)
    netbox_account_id = models.PositiveIntegerField(blank=True, null=True)
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
    invoice_prefix = models.CharField(max_length=64, blank=True, null=True, unique=True)
    next_invoice_id = models.PositiveIntegerField(default=1)
    crypto_allowed = models.BooleanField(blank=True, default=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._virtual_uk_bank = None
        self._virtual_us_bank = None

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
        balance = balance if balance != 0 else decimal.Decimal(0)
        return min(self.balance, balance)

    def balance_at(self, timestamp, item_id=None):
        queryset = self.ledgeritem_set \
            .filter(timestamp__lte=timestamp)

        if item_id:
            queryset = queryset.filter(Q(state=LedgerItem.STATE_COMPLETED) | Q(id=item_id))
        else:
            queryset = queryset.filter(state=LedgerItem.STATE_COMPLETED)

        balance = (
            queryset
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
        )
        customer_id = customer['id']
        self.stripe_customer_id = customer_id
        self.save()
        return customer_id

    def get_gocardless_id(self):
        if self.gocardless_customer_id:
            return self.gocardless_customer_id

        mandate_id = None

        if ach_mandate := self.achmandate_set.first():
            mandate_id = ach_mandate.mandate_id
        elif autogiro_mandate := self.autogiromandate_set.first():
            mandate_id = autogiro_mandate.mandate_id
        elif bacs_mandate := self.gcbacsmandate_set.first():
            mandate_id = bacs_mandate.mandate_id
        elif becs_mandate := self.becsmandate_set.first():
            mandate_id = becs_mandate.mandate_id
        elif becs_nz_mandate := self.becsnzmandate_set.first():
            mandate_id = becs_nz_mandate.mandate_id
        elif betalingsservice_mandate := self.betalingsservicemandate_set.first():
            mandate_id = betalingsservice_mandate.mandate_id
        elif pad_mandate := self.padmandate_set.first():
            mandate_id = pad_mandate.mandate_id
        elif sepa_mandate := self.gcsepamandate_set.first():
            mandate_id = sepa_mandate.mandate_id

        if mandate_id:
            mandate = apps.gocardless_client.mandates.get(mandate_id)
            customer_id = mandate.links.customer

            self.gocardless_customer_id = customer_id
            self.save()
            return customer_id

        return None

    @staticmethod
    def _gen_invoice_prefix():
        alphabet = string.ascii_uppercase + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(6))

    def get_invoice_prefix(self):
        if self.invoice_prefix:
            return self.invoice_prefix

        while True:
            poss_prefix = self._gen_invoice_prefix()

            if self.__class__.objects.filter(invoice_prefix=poss_prefix).count() != 0:
                continue

            self.invoice_prefix = poss_prefix
            self.save()

            return self.invoice_prefix

    def save(self, *args, **kwargs):
        if self.stripe_customer_id:
            t = threading.Thread(target=stripe.Customer.modify, args=(self.stripe_customer_id,), kwargs={
                "email": self.user.email,
                "name": f"{self.user.first_name} {self.user.last_name}",
                "address": {
                    "line1": self.billing_address.street_1,
                    "line2": self.billing_address.street_2,
                    "city": self.billing_address.city,
                    "state": self.billing_address.province,
                    "postal_code": self.billing_address.postal_code,
                    "country": self.billing_address.country_code.code,
                } if self.billing_address else None,
            })
            t.setDaemon(True)
            t.start()

        if self.gocardless_customer_id:
            t = threading.Thread(
                target=apps.gocardless_client.customers.update, args=(self.gocardless_customer_id,), kwargs={
                    "params": {
                        "email": self.user.email,
                        "family_name": self.user.last_name,
                        "given_name": self.user.first_name,
                        "company_name": self.billing_address.organisation if self.billing_address else None,
                        "address_line1": self.billing_address.street_1 if self.billing_address else None,
                        "address_line2": self.billing_address.street_2 if self.billing_address else None,
                        "address_line3": self.billing_address.street_3 if self.billing_address else None,
                        "city": self.billing_address.city if self.billing_address else None,
                        "region": self.billing_address.province if self.billing_address else None,
                        "postal_code": self.billing_address.postal_code if self.billing_address else None,
                        "country_code": self.billing_address.country_code.code if self.billing_address else None
                    }
                }
            )
            t.setDaemon(True)
            t.start()

        super().save(*args, **kwargs)


    def merge_account(
            self,
            old_account  # type: Account
    ):
        for s in (
                old_account.ledgeritem_set, old_account.accountbillingaddress_set, old_account.achmandate_set,
                old_account.autogiromandate_set, old_account.bacsmandate_set, old_account.becsmandate_set,
                old_account.becsnzmandate_set, old_account.betalingsservicemandate_set, old_account.gcbacsmandate_set,
                old_account.gcsepamandate_set, old_account.padmandate_set, old_account.sepamandate_set,
                old_account.chargestate_set, old_account.freeagentinvoice_set, old_account.knownbankaccount_set,
                old_account.knownstripepaymentmethod_set, old_account.notificationsubscription_set,
                old_account.subscription_set
        ):
            for item in s.all():
                item.account = self
                item.save()
        if not self.stripe_customer_id:
            self.stripe_customer_id = old_account.stripe_customer_id
            self.save()
        if not self.freeagent_contact_id:
            self.freeagent_contact_id = old_account.freeagent_contact_id
            self.save()
        if not self.billing_address:
            self.billing_address = old_account.billing_address
            self.save()
        old_account.user.delete()
        return self

    @property
    def taxable(self):
        if not self.billing_address:
            return True
        else:
            if bool(self.billing_address.vat_id):
                if self.billing_address.country_code.code.lower() in vat.VAT_MOSS_COUNTRIES:
                    return False
                elif self.billing_address.country_code.code.lower() == "tr":
                    return False
                else:
                    return True
            else:
                return True

    @property
    def virtual_uk_bank(self):
        if self.billing_address:
            if self.billing_address.country_code.code.lower() == "gb" or not self.taxable:
                if self._virtual_uk_bank:
                    return self._virtual_uk_bank
                else:
                    cust_id = self.get_stripe_id()
                    addresses = stripe.Customer.create_funding_instructions(
                        cust_id,
                        funding_type="bank_transfer",
                        bank_transfer={"type": "gb_bank_transfer"},
                        currency="gbp",
                    )["bank_transfer"]["financial_addresses"]
                    uk_address = next(filter(lambda a: a["type"] == "sort_code", addresses), None)
                    if uk_address:
                        address = UKBankAddress(
                            sort_code=uk_address["sort_code"]["sort_code"],
                            account_number=uk_address["sort_code"]["account_number"],
                        )
                        self._virtual_uk_bank = address
                        return address

        return None

    @property
    def virtual_us_bank(self):
        return None

        # Stripe seems to have just pulled this from under our feet, what fun!
        # if self.billing_address:
        #     if self.billing_address.country_code.code.lower() == "us" or not self.taxable:
        #         if self._virtual_us_bank:
        #             return self._virtual_us_bank
        #         else:
        #             cust_id = self.get_stripe_id()
        #             addresses = stripe.Customer.create_funding_instructions(
        #                 cust_id,
        #                 funding_type="bank_transfer",
        #                 bank_transfer={"type": "us_bank_transfer"},
        #                 currency="usd",
        #             )["bank_transfer"]["financial_addresses"]
        #             us_address = next(filter(lambda a: a["type"] == "aba", addresses), None)
        #             if us_address:
        #                 address = USBankAddress(
        #                     account_number=us_address["aba"]["account_number"],
        #                     routing_number=us_address["aba"]["routing_number"],
        #                     bank_name=us_address["aba"]["bank_name"],
        #                 )
        #                 self._virtual_us_bank = address
        #                 return address
        #
        # return None

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
def create_user_profile(instance, created, **kwargs):
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


@dataclasses.dataclass
class UKBankAddress:
    sort_code: str
    account_number: str

    @property
    def formatted_sort_code(self):
        return f"{self.sort_code[0:2]}-{self.sort_code[2:4]}-{self.sort_code[4:6]}"


@dataclasses.dataclass
class USBankAddress:
    routing_number: str
    account_number: str
    bank_name: str


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


class AbstractMandate(models.Model):
    account_mandate_attr = None
    account_mandate_attrs = (
        'default_stripe_payment_method_id', 'default_ach_mandate', 'default_autogiro_mandate',
        'default_bacs_mandate', 'default_gc_bacs_mandate', 'default_becs_mandate',
        'default_becs_nz_mandate', 'default_betalingsservice_mandate',
        'default_pad_mandate', 'default_sepa_mandate', 'default_gc_sepa_mandate'
    )

    id = as207960_utils.models.TypedUUIDField('billing_mandate', primary_key=True)
    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True)
    active = models.BooleanField(default=False)

    class Meta:
        abstract = True

    @classmethod
    def has_active_mandate(cls, account):
        return any(bool(getattr(account, a, None)) for a in cls.account_mandate_attrs)


class StripeMandate(AbstractMandate):
    mandate_id = models.CharField(max_length=255)
    payment_method = models.CharField(max_length=255)

    @classmethod
    def sync_mandate(cls, mandate_id, account):
        mandate_obj = cls.objects.filter(mandate_id=mandate_id).first()
        mandate = stripe.Mandate.retrieve(mandate_id)
        is_active = mandate["status"] == "active"

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
            if not is_active and mandate == getattr(mandate_obj.account, cls.account_mandate_attr, None):
                setattr(mandate_obj.account, cls.account_mandate_attr, None)
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

        if is_active and account and not cls.has_active_mandate(account):
            if cls.account_mandate_attr:
                setattr(account, cls.account_mandate_attr, mandate_obj)
                account.save()

        return mandate_obj

    class Meta:
        abstract = True


class GCMandate(AbstractMandate):
    mandate_id = models.CharField(max_length=255)

    @classmethod
    def sync_mandate(cls, mandate_id, account):
        mandate_obj = cls.objects.filter(mandate_id=mandate_id).first()
        mandate = apps.gocardless_client.mandates.get(mandate_id)
        is_active = mandate.status in (
            "pending_customer_approval", "pending_submission", "submitted", "active"
        )
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
            if cls.account_mandate_attr:
                if not is_active and mandate == getattr(mandate_obj.account, cls.account_mandate_attr, None):
                    setattr(mandate_obj.account, cls.account_mandate_attr, None)
                    mandate_obj.account.save()
            mandate_obj.save()

        if is_active and account and not cls.has_active_mandate(account):
            if cls.account_mandate_attr:
                setattr(account, cls.account_mandate_attr, mandate_obj)
                account.save()

        return mandate_obj

    class Meta:
        abstract = True


class ACHMandate(GCMandate):
    account_mandate_attr = "default_ach_mandate"

    class Meta:
        verbose_name = "ACH Mandate"
        verbose_name_plural = "ACH Mandates"


class AutogiroMandate(GCMandate):
    account_mandate_attr = "default_autogiro_mandate"

    class Meta:
        verbose_name = "Autogiro Mandate"
        verbose_name_plural = "Autogiro Mandates"


class BACSMandate(StripeMandate):
    account_mandate_attr = "default_bacs_mandate"

    class Meta:
        verbose_name = "Stripe BACS Mandate"
        verbose_name_plural = "Stripe BACS Mandates"


class GCBACSMandate(GCMandate):
    account_mandate_attr = "default_gc_bacs_mandate"

    class Meta:
        verbose_name = "GoCardless BACS Mandate"
        verbose_name_plural = "GoCardless BACS Mandates"


class BECSMandate(GCMandate):
    account_mandate_attr = "default_becs_mandate"

    class Meta:
        verbose_name = "BECS Mandate"
        verbose_name_plural = "BECS Mandates"


class BECSNZMandate(GCMandate):
    account_mandate_attr = "default_becs_nz_mandate"

    class Meta:
        verbose_name = "BECS NZ Mandate"
        verbose_name_plural = "BECS NZ Mandates"


class BetalingsserviceMandate(GCMandate):
    account_mandate_attr = "default_betalingsservice_mandate"

    class Meta:
        verbose_name = "Betalingsservice Mandate"
        verbose_name_plural = "Betalingsservice Mandates"


class PADMandate(GCMandate):
    account_mandate_attr = "default_pad_mandate"

    class Meta:
        verbose_name = "PAD Mandate"
        verbose_name_plural = "PAD Mandates"


class SEPAMandate(StripeMandate):
    account_mandate_attr = "default_sepa_mandate"

    class Meta:
        verbose_name = "Stripe SEPA Mandate"
        verbose_name_plural = "Stripe SEPA Mandates"


class GCSEPAMandate(GCMandate):
    account_mandate_attr = "default_gc_sepa_mandate"

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
    TYPE_GOCARDLESS_PR = "L"
    TYPE_CRYPTO = "Y"
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
        (TYPE_GOCARDLESS_PR, "GoCardless payment request"),
        (TYPE_CRYPTO, "Coinbase Crypto"),
    )

    id = as207960_utils.models.TypedUUIDField('billing_ledgeritem', primary_key=True)
    account = models.ForeignKey(Account, on_delete=models.CASCADE, null=True)
    invoice_id = models.PositiveIntegerField(blank=True, null=True)
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
    stripe_climate_contribution = models.DecimalField(decimal_places=2, max_digits=9, default=0)
    eur_exchange_rate = models.DecimalField(decimal_places=7, max_digits=20, blank=True, null=True)
    try_exchange_rate = models.DecimalField(decimal_places=7, max_digits=20, blank=True, null=True)
    krw_exchange_rate = models.DecimalField(decimal_places=7, max_digits=20, blank=True, null=True)
    subscription_charge = models.ForeignKey('SubscriptionCharge', on_delete=models.PROTECT, blank=True, null=True,
                                            related_name='ledger_items')
    payment_charge_state = models.ForeignKey(
        'ChargeState', on_delete=models.SET_NULL, blank=True, null=True, related_name='payment_items')

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

    def save(self, mail=True, force_mail=False, *args, **kwargs):
        if self.state == self.STATE_COMPLETED and not self.completed_timestamp:
            self.completed_timestamp = timezone.now()
        if self.state != self.original_state:
            self.last_state_change_timestamp = timezone.now()

        super().save(*args, **kwargs)

        from . import tasks
        tasks.try_update_charge_state(instance=self, mail=mail, force_mail=force_mail)

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
            exchange_rate = payment_amount / self.amount
            total_refunded = (total_refunded_local / exchange_rate).quantize(decimal.Decimal("1.00"))
            return min(self.amount - total_refunded, account_refundable)
        else:
            return decimal.Decimal(0)

    @property
    def balance_at(self):
        return self.account.balance_at(self.timestamp, self.id)

    def get_invoice_id(self):
        invoice_prefix = self.account.get_invoice_prefix()

        if self.invoice_id:
            return f"{invoice_prefix}-{self.invoice_id:04d}"

        with transaction.atomic():
            self.invoice_id = self.account.next_invoice_id
            self.account.next_invoice_id += 1
            self.account.save()
            self.save(mail=False)

        return f"{invoice_prefix}-{self.invoice_id:04d}"

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
    amount = models.DecimalField(decimal_places=2, max_digits=9, default=0)

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

    def freeagent_invoice(self):
        try:
            return self.freeagentinvoice
        except django.core.exceptions.ObjectDoesNotExist:
            return None

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
    def next_bill_attempt(self):
        from . import tasks
        attempted = self.last_bill_attempted
        if attempted is not None:
            return attempted + tasks.SUBSCRIPTION_RETRY_INTERVAL
        else:
            return None

    @property
    def last_bill_subscription_charge(self):
        return self.subscriptioncharge_set.order_by('-timestamp').first()

    @property
    def last_non_setup_bill_subscription_charge(self):
        return self.subscriptioncharge_set.order_by('-timestamp').filter(is_setup_charge=False).first()

    @property
    def last_bill_attempted(self):
        if charge := self.last_non_setup_bill_subscription_charge:
            return charge.last_bill_attempted
        if self.last_bill_subscription_charge:
            return self.last_bill_subscription_charge.last_bill_attempted
        return None

    @property
    def failed_bill_attempts(self):
        if charge := self.last_non_setup_bill_subscription_charge:
            return charge.failed_bill_attempts
        if self.last_bill_subscription_charge:
            return self.last_bill_subscription_charge.failed_bill_attempts
        return None

    @property
    def usage_in_period_label(self):
        usage = self.usage_in_period
        return f"{usage} {p.plural(self.plan.unit_label, usage)}"

    @property
    def amount_unpaid(self):
        return (
                self.subscriptioncharge_set
                .filter(last_ledger_item__state=LedgerItem.STATE_FAILED)
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
    amount = models.DecimalField(decimal_places=2, max_digits=9, default=0)
    last_ledger_item = models.OneToOneField(LedgerItem, on_delete=models.PROTECT)
    is_setup_charge = models.BooleanField(blank=True, default=False)

    @property
    def failed_bill_attempts(self):
        return self.ledger_items.filter(state=LedgerItem.STATE_FAILED).count()

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


class FreeagentInvoice(models.Model):
    id = as207960_utils.models.TypedUUIDField('billing_freeagentinvoice', primary_key=True)
    account = models.ForeignKey(Account, on_delete=models.CASCADE, blank=True, null=True)
    temp_account = models.BooleanField(blank=True)
    charge_state = models.OneToOneField(ChargeState, on_delete=models.PROTECT, blank=True, null=True)
    freeagent_id = models.CharField(max_length=255)
