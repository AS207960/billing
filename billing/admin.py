from django.contrib import admin
from . import models


admin.site.register(models.Account)
admin.site.register(models.LedgerItem)
admin.site.register(models.ExchangeRate)
admin.site.register(models.SEPAMandate)
admin.site.register(models.BACSMandate)
admin.site.register(models.ChargeState)
admin.site.register(models.KnownBankAccount)
admin.site.register(models.KnownStripePaymentMethod)


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
