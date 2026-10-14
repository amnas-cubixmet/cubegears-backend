from django.contrib import admin
from .models import (
    FeatureGroup, PlanFeature, SubscriptionPlan, PlanFeatureAccess,
    PlanChangeRequest, CustomPlanRequest,
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
    list_display = ("name", "key", "monthly_price", "yearly_price", "max_branches", "max_users", "is_active")
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
