from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

from apps.search.views import WorkspaceSearchView
from apps.reports.views import DashboardView
from apps.saas.views import SettingsView

class HealthView(APIView):
    # Public liveness: no authentication, tenant records or configuration secrets.
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        response = Response({
            "status": "ok",
            "service": "cubegears-backend",
            "api_version": "v1",
            "server_time": timezone.now().isoformat(),
            "request_id": getattr(request, "request_id", None),
        })
        response["Cache-Control"] = "no-store"
        return response

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.control_panel.urls")),
    path("api/v1/health/", HealthView.as_view(), name="health"),
    path("api/v1/search", WorkspaceSearchView.as_view(), name="workspace-search"),
    path("api/v1/customer-website/", include("apps.customers.website_urls")),
    path("api/v1/", include("apps.customers.portal_urls")),
    path("api/v1/", include("apps.vehicles.photo_urls")),
    path("api/v1/whatsapp/", include("apps.whatsapp.urls")),

    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/company/", include("apps.companies.urls")),
    path("api/v1/branches/", include("apps.branches.urls")),
    path("api/v1/roles/", include("apps.roles.urls")),

    re_path(r"^api/v1/customers/?", include("apps.customers.urls")),
    re_path(r"^api/v1/vehicles/?", include("apps.vehicles.urls")),
    re_path(r"^api/v1/services/?", include("apps.services.urls")),
    re_path(r"^api/v1/jobs/?", include("apps.jobs.urls")),
    re_path(r"^api/v1/outside-labour/?", include("apps.jobs.outside_labour_urls")),
    path("api/v1/", include("apps.jobs.compat_urls")),

    # Canonical Supplier APIs; stock/inventory procurement routes remain compatible.
    re_path(r"^api/v1/suppliers(?:/|$)", include("apps.suppliers.urls")),
    re_path(r"^api/v1/stock/?", include("apps.inventory.urls")),
    re_path(r"^api/v1/inventory/?", include("apps.inventory.urls")),

    re_path(r"^api/v1/invoices/?", include("apps.invoices.urls")),
    re_path(r"^api/v1/e-way-bills/?", include("apps.invoices.eway_urls")),
    re_path(r"^api/v1/billing/documents/?", include("apps.invoices.urls")),
    re_path(r"^api/v1/payments/?", include("apps.payments.urls")),
    re_path(r"^api/v1/expenses/?", include("apps.expenses.urls")),

    re_path(r"^api/v1/employees/?", include("apps.employees.urls")),
    re_path(r"^api/v1/attendance/?", include("apps.attendance.urls")),
    path("api/v1/", include("apps.attendance.compat_urls")),
    re_path(r"^api/v1/payroll/?", include("apps.payroll.urls")),

    path("api/v1/gstr/", include("apps.gstr.urls")),
    path("api/v1/dashboard", DashboardView.as_view(), name="dashboard"),
    re_path(r"^api/v1/reports/?", include("apps.reports.urls")),
    re_path(r"^api/v1/notifications/?", include("apps.notifications.urls")),

    path("api/v1/settings", SettingsView.as_view(), name="settings-all"),
    path("api/v1/settings/<str:category>", SettingsView.as_view(), name="settings-category"),
    path("api/v1/saas/credits/", include("apps.credits.urls")),
    path("api/v1/saas/", include("apps.saas.urls")),
    path("api/v1/finance/", include("apps.finance.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
