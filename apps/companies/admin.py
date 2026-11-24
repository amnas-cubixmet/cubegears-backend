from django.contrib import admin
from .models import Company, CompanyWebsiteSettings, CompanyWebsiteSubdomain
from .subdomains import ensure_default_subdomain


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "plan", "currency", "is_active")
    search_fields = ("name", "legal_name", "email", "gstin")
    prepopulated_fields = {"slug": ("name",)}

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            ensure_default_subdomain(obj)


@admin.register(CompanyWebsiteSettings)
class CompanyWebsiteSettingsAdmin(admin.ModelAdmin):
    list_display = ("company", "public_name", "updated_at")
    search_fields = ("company__name", "public_name")



@admin.register(CompanyWebsiteSubdomain)
class CompanyWebsiteSubdomainAdmin(admin.ModelAdmin):
    """Platform audit view; tenant website labels are changed via owner API."""
    list_display = ("company", "label", "website_enabled", "updated_at")
    list_filter = ("website_enabled",)
    search_fields = ("company__name", "company__slug", "label")
    readonly_fields = ("company", "label", "website_enabled", "created_at", "updated_at")

    def has_module_permission(self, request):
        return platform_superuser(request.user)

    def has_view_permission(self, request, obj=None):
        return platform_superuser(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

def platform_superuser(user):
    return bool(
        user.is_authenticated and user.is_active and user.is_staff
        and user.is_superuser and user.company_id is None
    )
