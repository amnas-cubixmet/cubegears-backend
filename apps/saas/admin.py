from django.contrib import admin
from .models import (
    FeatureGroup, PlanFeature, SubscriptionPlan, PlanFeatureAccess,
    PlanChangeRequest, CustomPlanRequest, RazorpayPlanPayment,
)

@admin.register(FeatureGroup)
class FeatureGroupAdmin(admin.ModelAdmin):
    list_display = ("label", "key", "parent", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("label", "key")

@admin.register(PlanFeature)
class PlanFeatureAdmin(admin.ModelAdmin):
    list_display = ("label", "key", "group", "is_active")
    list_filter = ("group", "is_active")
    search_fields = ("label", "key")

class PlanFeatureInline(admin.TabularInline):
    model = PlanFeatureAccess
    extra = 0

@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "monthly_price", "yearly_price", "access_duration_days", "max_branches", "max_users", "is_active")
    search_fields = ("name", "key")
    list_filter = ("is_active", "is_public")
    inlines = [PlanFeatureInline]

@admin.register(PlanChangeRequest)
class PlanChangeRequestAdmin(admin.ModelAdmin):
    list_display = ("company", "requested_plan", "billing_cycle", "status", "created_at")
    list_filter = ("status",)
    readonly_fields = ("company", "requested_by", "requested_plan", "billing_cycle", "message", "created_at")

@admin.register(CustomPlanRequest)
class CustomPlanRequestAdmin(admin.ModelAdmin):
    list_display = ("company", "email", "branches", "users", "status", "created_at")
    list_filter = ("status",)
    readonly_fields = ("company", "requested_by", "email", "branches", "users", "message", "created_at")

@admin.register(RazorpayPlanPayment)
class RazorpayPlanPaymentAdmin(admin.ModelAdmin):
    """Platform administrators may inspect the gateway ledger, never forge capture."""
    list_display = ("company", "plan", "amount_paise", "currency", "status", "payment_method", "paid_at", "period_end")
    search_fields = ("receipt", "razorpay_order_id", "razorpay_payment_id", "company__name")
    list_filter = ("status", "cycle", "currency")
    ordering = ("-created_at",)
    readonly_fields = (
        "id", "company", "branch", "requested_by", "subscription", "plan", "cycle",
        "validity_days", "amount_paise", "currency", "receipt", "razorpay_order_id",
        "razorpay_payment_id", "status", "payment_method", "paid_at",
        "period_start", "period_end", "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
