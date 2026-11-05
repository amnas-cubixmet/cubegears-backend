from django.contrib import admin
from .models import Company, CompanyDomain, CompanyWebsiteSettings

@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "plan", "currency", "is_active")
    search_fields = ("name", "legal_name", "email", "gstin")
    prepopulated_fields = {"slug": ("name",)}

@admin.register(CompanyWebsiteSettings)
class CompanyWebsiteSettingsAdmin(admin.ModelAdmin):
    list_display = ("company", "public_name", "updated_at")
    search_fields = ("company__name", "public_name")

@admin.register(CompanyDomain)
class CompanyDomainAdmin(admin.ModelAdmin):
    list_display = ("hostname", "company", "status", "is_primary", "verified_at")
    list_filter = ("status", "is_primary")
    search_fields = ("hostname", "company__name")
    # Provider-verified activation is intentionally not editable through staff API.
    # Platform administrators must verify DNS ownership and SSL before setting Active.
