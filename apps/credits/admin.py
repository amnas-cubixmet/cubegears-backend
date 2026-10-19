from django.contrib import admin
from .models import (
    CreditWallet, CreditPackage, FeatureCreditPrice,
    CompanyCreditFeature, CreditLedger, CreditPurchase, CreditWebhookEvent,
)


@admin.register(CreditPackage)
class CreditPackageAdmin(admin.ModelAdmin):
    list_display = ("name", "credits", "price_inr", "is_active", "is_archived", "sort_order")


@admin.register(FeatureCreditPrice)
class FeatureCreditPriceAdmin(admin.ModelAdmin):
    list_display = ("feature", "base_credits", "per_staff_credits", "is_available")


@admin.register(CreditWallet)
class CreditWalletAdmin(admin.ModelAdmin):
    list_display = ("company", "balance", "low_threshold", "updated_at")
    readonly_fields = ("balance", "company", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CreditLedger)
class CreditLedgerAdmin(admin.ModelAdmin):
    list_display = ("company", "kind", "delta", "balance_after", "reference", "created_at")
    readonly_fields = ("id", "company", "kind", "description", "delta", "balance_after", "reference", "actor", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CreditPurchase)
class CreditPurchaseAdmin(admin.ModelAdmin):
    list_display = ("company", "package", "credits", "amount_paise", "status", "order_id", "payment_id")
    readonly_fields = ("id", "company", "package", "requested_by", "credits", "amount_paise", "status",
                       "receipt", "order_id", "payment_id", "created_at", "paid_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(CompanyCreditFeature)
admin.site.register(CreditWebhookEvent)
