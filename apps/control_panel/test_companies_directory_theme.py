"""Company directory visual refactor and server-side filter regression tests."""
from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User
from apps.companies.models import Company


class CompaniesDirectoryThemeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="company-theme-admin@example.com", name="Theme Admin",
            password="Test-admin-823!",
        )
        cls.active_company = Company.objects.create(
            name="Alpha Workshop", slug="alpha-workshop", is_active=True,
        )
        cls.inactive_company = Company.objects.create(
            name="Beta Workshop", slug="beta-workshop", is_active=False,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_directory_renders_metrics_and_management_actions(self):
        response = self.client.get(reverse("panel:companies"))
        self.assertEqual(response.status_code, 200)
        for phrase in (
            "Companies Management", "Total Companies", "Active Companies",
            "Disabled Companies", "New This Month", "Company Directory",
            "Add Company", "Alpha Workshop", "Beta Workshop",
        ):
            self.assertContains(response, phrase)
        self.assertEqual(response.context["total_companies"], 2)
        self.assertEqual(response.context["active_companies"], 1)
        self.assertEqual(response.context["inactive_companies"], 1)
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'name="status"')

    def test_stat_numbers_and_captions_have_separate_layout(self):
        response = self.client.get(reverse("panel:companies"))
        self.assertContains(response, 'class="company-stat-value"', count=4)
        self.assertContains(response, 'class="company-stat-meta"', count=4)

        stylesheet_path = finders.find("control_panel/company-theme.css")
        self.assertIsNotNone(stylesheet_path)
        css = Path(stylesheet_path).read_text(encoding="utf-8")
        self.assertRegex(
            css,
            r"(?s)\.companies-stats \.company-stat-value\s*\{[^}]*display:block",
        )
        self.assertRegex(
            css,
            r"(?s)\.companies-stats \.company-stat-meta\s*\{[^}]*display:block",
        )

    def test_status_filter_keeps_counts_global(self):
        result = self.client.get(reverse("panel:companies"), {"status": "inactive"})
        self.assertEqual(result.context["rows"].paginator.count, 1)
        self.assertContains(result, "Beta Workshop")
        self.assertNotContains(result, "Alpha Workshop")
        self.assertEqual(result.context["total_companies"], 2)

    def test_search_and_status_can_be_combined(self):
        result = self.client.get(reverse("panel:companies"), {
            "q": "Alpha", "status": "active",
        })
        self.assertEqual(result.context["rows"].paginator.count, 1)
        result = self.client.get(reverse("panel:companies"), {
            "q": "Alpha", "status": "inactive",
        })
        self.assertEqual(result.context["rows"].paginator.count, 0)
        self.assertContains(result, "No companies match the current filters")

    def test_unauthenticated_user_redirected(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("panel:companies")).status_code, 302)
