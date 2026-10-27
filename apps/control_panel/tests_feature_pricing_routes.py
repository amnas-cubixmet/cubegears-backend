"""Regression checks for the single feature-pricing settings route."""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import Resolver404, resolve, reverse
from django.template.loader import get_template


class FeaturePricingSettingsRoutesTests(SimpleTestCase):
    def test_pricing_settings_remains_accessible(self):
        self.assertEqual(reverse("panel:feature-pricing"), "/features/pricing/")
        self.assertEqual(resolve("/features/pricing/").url_name, "feature-pricing")

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
