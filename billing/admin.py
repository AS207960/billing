from django.contrib import admin
from . import models


admin.site.register(models.Account)
admin.site.register(models.AccountBillingAddress)
admin.site.register(models.ExchangeRate)
admin.site.register(models.SEPAMandate)
admin.site.register(models.GCSEPAMandate)
admin.site.register(models.BACSMandate)
admin.site.register(models.GCBACSMandate)
admin.site.register(models.AutogiroMandate)
admin.site.register(models.ACHMandate)
admin.site.register(models.BECSMandate)
admin.site.register(models.BECSNZMandate)
admin.site.register(models.BetalingsserviceMandate)
admin.site.register(models.PADMandate)
admin.site.register(models.KnownBankAccount)
admin.site.register(models.KnownStripePaymentMethod)
admin.site.register(models.AccountStripeVirtualUKBank)


class LedgerItemAdmin(admin.ModelAdmin):
    readonly_fields = ('id',)
    ordering = ('-timestamp',)
    list_display = (
        'id', 'amount', 'descriptor', 'vat_rate', 'account'
    )
    list_filter = ('state',)


admin.site.register(models.LedgerItem, LedgerItemAdmin)


class ChargeStateAdmin(admin.ModelAdmin):
    readonly_fields = ('id',)
    ordering = ('-ledger_item__timestamp',)
    list_display = (
        'id', 'amount', 'get_ledger_item_timestamp', 'get_ledger_item_state', 'account', 'ready_to_complete',
        'can_reject'
    )
    list_filter = ('ledger_item__state',)

    def get_ledger_item_timestamp(self, obj: models.ChargeState):
        return obj.ledger_item.timestamp

    def get_ledger_item_state(self, obj: models.ChargeState):
        return obj.ledger_item.get_state_display()


admin.site.register(models.ChargeState, ChargeStateAdmin)


class RecurringPlanTierInline(admin.TabularInline):
    model = models.RecurringPlanTier
    extra = 0


class RecurringPlanAdmin(admin.ModelAdmin):
    inlines = [RecurringPlanTierInline]
    readonly_fields = ('id',)


class SubscriptionUsageInline(admin.TabularInline):
    model = models.SubscriptionUsage
    extra = 0


class SubscriptionChargeInline(admin.TabularInline):
    model = models.SubscriptionCharge
    extra = 0


class SubscriptionAdmin(admin.ModelAdmin):
    inlines = [SubscriptionUsageInline, SubscriptionChargeInline]
    readonly_fields = ('id',)


admin.site.register(models.RecurringPlan, RecurringPlanAdmin)
admin.site.register(models.Subscription, SubscriptionAdmin)


class InvoiceFeeInline(admin.TabularInline):
    readonly_fields = ('id',)
    model = models.InvoiceFee
    extra = 3


class InvoiceDiscountInline(admin.TabularInline):
    readonly_fields = ('id',)
    model = models.InvoiceDiscount
    extra = 3


class InvoiceAdmin(admin.ModelAdmin):
    inlines = [InvoiceFeeInline, InvoiceDiscountInline]
    readonly_fields = ('id',)


admin.site.register(models.Invoice, InvoiceAdmin)
