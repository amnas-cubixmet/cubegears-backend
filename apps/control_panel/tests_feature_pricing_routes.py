"""Regression checks for the single feature-pricing settings route."""
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import Resolver404, resolve, reverse
from django.template.loader import get_template
from apps.accounts.models import User
from apps.companies.models import Company


class FeaturePricingSettingsRoutesTests(SimpleTestCase):
    def test_pricing_settings_remains_accessible(self):
        self.assertEqual(reverse("panel:feature-pricing"), "/features/pricing/")
        self.assertEqual(resolve("/features/pricing/").url_name, "feature-pricing")

    def test_record_urls_use_numeric_references_and_accept_old_links(self):
        pk = UUID("00000000-0000-0000-0000-000000000042")
        for route, section in (("company-detail", "companies"), ("user-edit", "users"),
                               ("staff-detail", "staff"), ("role-delete", "roles")):
            with self.subTest(route=route):
                link = reverse("panel:" + route, kwargs={"pk": pk})
                self.assertIn(f"/{section}/ref-66/", link)
                self.assertEqual(resolve(link).kwargs["pk"], pk)
                self.assertEqual(resolve(link.replace("ref-66", str(pk))).kwargs["pk"], pk)

    def test_legacy_feature_editor_and_sync_routes_are_removed(self):
        retired_routes = (
            "/features/",
            "/features/sync/",
            "/features/add/",
            "/features/1/edit/",
            "/features/groups/add/",
            "/features/groups/1/edit/",
            "/features/bundles/add/",
            "/features/bundles/1/edit/",
        )
        for url in retired_routes:
            with self.subTest(url=url), self.assertRaises(Resolver404):
                resolve(url)

    def test_pricing_template_keeps_only_settings_controls(self):
        get_template("control_panel/module_pricing.html")
        content = (
            Path(settings.BASE_DIR) / "templates/control_panel/module_pricing.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Feature Pricing Settings", content)
        self.assertIn("Save price", content)
        self.assertIn("row.form.published", content)
        self.assertIn("row.form.monthly_postpaid", content)
        self.assertNotIn("feature-management", content)
        self.assertNotIn("panel:features-edit", content)
        self.assertNotIn("panel:feature-create", content)
        self.assertNotIn("panel:featurebundle-create", content)
        self.assertNotIn('aria-label="Related settings"', content)


class ControlPanelRecordLinkTests(TestCase):
    def test_company_links_and_filters_redirect_old_urls_to_the_same_record(self):
        admin = User.objects.create_superuser(email="references@example.test", password="test-only")
        owner = User.objects.create_user(email="workshop-owner@example.test", name="Workshop Owner")
        company = Company.objects.create(name="Reference Workshop", slug="reference-workshop", owner=owner)
        self.client.force_login(admin)
        link = reverse("panel:company-detail", kwargs={"pk": company.pk})
        directory = self.client.get(reverse("panel:companies"))
        self.assertContains(directory, f'href="{link}"')
        self.assertNotContains(directory, str(company.pk))
        self.assertRedirects(self.client.get(f"/companies/{company.pk}/"), link, fetch_redirect_response=False)
        self.assertContains(self.client.get(link), company.name)
        selection = link.strip("/").split("/")[-1]
        users = reverse("panel:users")
        filtered = f"{users}?company={selection}"
        self.assertRedirects(self.client.get(f"{users}?company={company.pk}"), filtered)
        self.assertContains(self.client.get(filtered), f'value="{selection}" selected')
