from django.contrib import admin
from . import models


admin.site.register(models.Account)
admin.site.register(models.LedgerItem)
admin.site.register(models.ExchangeRate)
admin.site.register(models.SEPAMandate)
admin.site.register(models.BACSMandate)
admin.site.register(models.ChargeState)


class RecurringPlanTierInline(admin.TabularInline):
    model = models.RecurringPlanTier
    extra = 0


class RecurringPlanAdmin(admin.ModelAdmin):
    inlines = [RecurringPlanTierInline]
    readonly_fields = ('id',)


class SubscriptionUsageInline(admin.TabularInline):
    model = models.SubscriptionUsage
    extra = 0


class SubscriptionAdmin(admin.ModelAdmin):
    inlines = [SubscriptionUsageInline]
    readonly_fields = ('id',)


admin.site.register(models.RecurringPlan, RecurringPlanAdmin)
admin.site.register(models.Subscription, SubscriptionAdmin)
