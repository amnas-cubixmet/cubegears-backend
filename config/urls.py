from django.contrib import admin
from django.urls import include, path
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
    path("api/v1/health/", HealthView.as_view(), name="health"),

    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/company/", include("apps.companies.urls")),
    path("api/v1/branches/", include("apps.branches.urls")),
    path("api/v1/roles/", include("apps.roles.urls")),

    path("api/v1/customers", include("apps.customers.urls")),
    path("api/v1/vehicles", include("apps.vehicles.urls")),
    path("api/v1/services", include("apps.services.urls")),
    path("api/v1/jobs", include("apps.jobs.urls")),

    path("api/v1/stock", include("apps.inventory.urls")),
    path("api/v1/inventory", include("apps.inventory.urls")),

    path("api/v1/invoices", include("apps.invoices.urls")),
    path("api/v1/billing/documents", include("apps.invoices.urls")),
    path("api/v1/payments", include("apps.payments.urls")),
    path("api/v1/expenses", include("apps.expenses.urls")),

    path("api/v1/employees", include("apps.employees.urls")),
    path("api/v1/attendance/", include("apps.attendance.urls")),
    path("api/v1/payroll", include("apps.payroll.urls")),

    path("api/v1/dashboard", DashboardView.as_view(), name="dashboard"),
    path("api/v1/reports", include("apps.reports.urls")),
    path("api/v1/notifications", include("apps.notifications.urls")),

    path("api/v1/settings", SettingsView.as_view(), name="settings-all"),
    path("api/v1/settings/<str:category>", SettingsView.as_view(), name="settings-category"),
    path("api/v1/saas/", include("apps.saas.urls")),
]
