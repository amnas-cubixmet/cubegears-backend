"""Render and access-control regression checks for the company-inspired SaaS dashboard."""

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User


class CompanyInspiredDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="dashboard-style@example.com",
            name="Dashboard Admin",
            password="Secure-style-123!",
        )

    def test_company_dashboard_assets_exist(self):
        for filename in ("control_panel/company-theme.css", "control_panel/icons.svg"):
            self.assertIsNotNone(finders.find(filename), filename)

    def test_dashboard_keeps_live_metrics_and_permissions(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("panel:dashboard"))
        self.assertEqual(response.status_code, 200)
        for fragment in (
            "control_panel/company-theme.css",
            "control_panel/icons.svg",
            "Welcome to your dashboard",
            "Total Companies",
            "Platform Users",
            "Active Plans",
            "Recent companies",
            "Recent activity",
            "data-theme-set",
            "data-sidebar-toggle",
        ):
            self.assertContains(response, fragment)

    def test_dashboard_still_requires_authentication(self):
        self.assertEqual(self.client.get(reverse("panel:dashboard")).status_code, 302)

    def test_theme_assets_are_separate_from_login_screen(self):
        response = self.client.get(reverse("panel:login"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-sidebar-toggle")
