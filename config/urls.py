from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.reports.views import DashboardView
from apps.saas.views import SettingsView

class HealthView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "ok", "service": "cubegears-backend"})

urlpatterns = [
    path("admin/", admin.site.urls),
    path("panel/", include("apps.control_panel.urls")),
    path("api/v1/health/", HealthView.as_view(), name="health"),

    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/company/", include("apps.companies.urls")),
    path("api/v1/branches/", include("apps.branches.urls")),
    path("api/v1/roles/", include("apps.roles.urls")),

    re_path(r"^api/v1/customers/?", include("apps.customers.urls")),
    re_path(r"^api/v1/vehicles/?", include("apps.vehicles.urls")),
    re_path(r"^api/v1/services/?", include("apps.services.urls")),
    re_path(r"^api/v1/jobs/?", include("apps.jobs.urls")),
    path("api/v1/", include("apps.jobs.compat_urls")),

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

    path("api/v1/dashboard", DashboardView.as_view(), name="dashboard"),
    re_path(r"^api/v1/reports/?", include("apps.reports.urls")),
    re_path(r"^api/v1/notifications/?", include("apps.notifications.urls")),

    path("api/v1/settings", SettingsView.as_view(), name="settings-all"),
    path("api/v1/settings/<str:category>", SettingsView.as_view(), name="settings-category"),
    path("api/v1/saas/", include("apps.saas.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
