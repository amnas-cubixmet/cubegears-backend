from django.contrib import admin, messages
from .models import Company, CompanyDomain, CompanyDomainAudit, CompanyWebsiteSettings
from .vercel_domains import VercelDomainError, record_domain_event, refresh_domain


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "plan", "currency", "is_active")
    search_fields = ("name", "legal_name", "email", "gstin")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(CompanyWebsiteSettings)
class CompanyWebsiteSettingsAdmin(admin.ModelAdmin):
    list_display = ("company", "public_name", "updated_at")
    search_fields = ("company__name", "public_name")


def platform_superuser(user):
    return bool(
        user.is_authenticated and user.is_active and user.is_staff
        and user.is_superuser and user.company_id is None
    )


@admin.register(CompanyDomain)
class CompanyDomainAdmin(admin.ModelAdmin):
    list_display = (
        "hostname", "company", "status", "dns_provider", "dns_configured",
        "ownership_verified", "ssl_status", "is_primary", "is_approved",
        "last_checked_at",
    )
    list_filter = ("status", "is_primary", "is_approved", "ssl_status", "dns_provider")
    search_fields = ("hostname", "company__name", "company__slug")
    readonly_fields = (
        "company", "hostname", "status", "is_primary", "verified_at",
        "provider_reference", "dns_provider", "dns_records", "verification_records",
        "dns_configured", "ownership_verified", "ssl_status", "last_error",
        "last_checked_at", "is_approved", "approved_by", "vercel_project_id",
        "created_at", "updated_at",
    )
    actions = ["approve_and_check", "disable_domains", "refresh_checks"]

    def has_module_permission(self, request):
        return platform_superuser(request.user)

    def has_view_permission(self, request, obj=None):
        return platform_superuser(request.user)

    def has_change_permission(self, request, obj=None):
        return platform_superuser(request.user)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Approve and check selected domains with Vercel")
    def approve_and_check(self, request, queryset):
        for domain in queryset.select_related("company"):
            domain.is_approved = True
            domain.approved_by = request.user
            domain.status = CompanyDomain.PENDING_SETUP
            domain.is_primary = False
            domain.save()
            record_domain_event(domain, request.user, "platform_approved")
            try:
                refresh_domain(domain, request.user, attach=True)
            except VercelDomainError as exc:
                self.message_user(request, f"{domain.hostname}: {exc}", level=messages.WARNING)

    @admin.action(description="Disable selected customer domains")
    def disable_domains(self, request, queryset):
        for domain in queryset.select_related("company"):
            domain.is_approved = False
            domain.status = CompanyDomain.DISABLED
            domain.is_primary = False
            domain.verified_at = None
            domain.ssl_status = "pending"
            domain.save()
            record_domain_event(domain, request.user, "platform_disabled")
        self.message_user(request, "Selected domains are disabled for customer access.")

    @admin.action(description="Refresh selected domain DNS and SSL status")
    def refresh_checks(self, request, queryset):
        for domain in queryset.select_related("company"):
            if domain.status == CompanyDomain.DISABLED or not domain.is_approved:
                continue
            try:
                refresh_domain(domain, request.user, attach=domain.status == CompanyDomain.PENDING_SETUP)
            except VercelDomainError as exc:
                self.message_user(request, f"{domain.hostname}: {exc}", level=messages.WARNING)


@admin.register(CompanyDomainAudit)
class CompanyDomainAuditAdmin(admin.ModelAdmin):
    list_display = ("created_at", "company", "domain", "action", "actor")
    list_filter = ("action", "created_at")
    search_fields = ("domain__hostname", "company__name")
    readonly_fields = ("domain", "company", "actor", "action", "details", "created_at")

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
