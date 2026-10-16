"""Legacy credit-panel pricing forms have been retired, including direct POSTs.

Feature Labels (/features/pricing/) and the dedicated Storage page remain
separate, functional systems. Keep historical rates for audit/migrations.
"""
from decimal import Decimal

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.credits.models import CreditPackage, FeatureCreditPrice, StorageCreditPolicy
from apps.saas.models import FeatureGroup, PlanFeature


class RetiredCreditPanelPricingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="old-price-admin@example.test", name="Platform Admin",
            password="Secure123456789!",
        )
        group = FeatureGroup.objects.create(key="retired-credit-tab-test", label="Operations")
        cls.feature = PlanFeature.objects.create(key="jobs", label="Jobs", group=group)
        cls.price = FeatureCreditPrice.objects.create(
            feature=cls.feature, base_credits=Decimal("45.00"),
        )
        cls.policy = StorageCreditPolicy.objects.create(
            billing_period="monthly", billing_mode="snapshot",
            included_gb=Decimal("1.000"), credits_per_gb=Decimal("12.00"),
            is_active=False,
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("panel:credits")

    def test_legacy_pricing_content_and_context_are_absent(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for removed in (
            "credit-panel-pricing", 'data-credit-tab="pricing"',
            'name="action" value="price"', 'name="action" value="storage"',
            "Set per-user prices for all modules",
            "Storage Credits — separate billing policy",
            "Save feature price", "Save Storage Pricing",
            "Credits per active employee / period",
            "Edit module prices", "Feature Prices",
        ):
            self.assertNotContains(response, removed)
        for preserved in (
            "Credit Wallet Control", "Credit Packages", "Manual Adjustment",
            "Wallet Balances", "Verified Purchases", "Credit Ledger",
        ):
            self.assertContains(response, preserved)
        self.assertNotIn("prices", response.context)
        self.assertNotIn("features", response.context)
        self.assertNotIn("storage_policy", response.context)
        self.assertNotIn("configured_features", response.context)

    def test_all_legacy_write_actions_are_gone_and_do_not_change_prices(self):
        for payload in (
            {
                "action": "price", "feature": str(self.feature.pk),
                "base_credits": "1.00", "billing_period": "daily",
                "is_available": "on",
            },
            {
                "action": "storage", "included_gb": "0",
                "credits_per_gb": "999.00",
                "billing_period": "daily", "is_active": "on",
            },
        ):
            response = self.client.post(self.url, payload)
            self.assertEqual(response.status_code, 410)
            self.price.refresh_from_db()
            self.policy.refresh_from_db()
            self.assertEqual(self.price.base_credits, Decimal("45.00"))
            self.assertEqual(self.price.billing_period, "monthly")
            self.assertEqual(self.policy.credits_per_gb, Decimal("12.00"))
            self.assertFalse(self.policy.is_active)

    def test_existing_package_create_and_audited_wallet_sections_continue_to_work(self):
        response = self.client.post(self.url, {
            "action": "package", "name": "Starter 100", "credits": "100",
            "price_inr": "100", "sort_order_mode": "unlimited",
            "is_active": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CreditPackage.objects.get(name="Starter 100").credits, Decimal("100"))

    def test_old_pricing_hash_and_keyboard_tabs_have_safe_fallback(self):
        js_path = finders.find("control_panel/credits-tabs.js")
        self.assertIsNotNone(js_path)
        with open(js_path, encoding="utf-8") as source:
            js = source.read()
        self.assertIn("buttons.length !== 5", js)
        self.assertIn('window.location.hash === "#credit-panel-pricing"', js)
        self.assertIn("credit-panel-packages", js)
        self.assertIn('event.key === "ArrowRight"', js)

    def test_active_newer_price_controls_are_not_accidentally_deleted(self):
        # Feature Labels still work, with existing permission controls, on
        # their own management page. Likewise, Storage has its own page.
        self.assertEqual(self.client.get(reverse("panel:feature-pricing")).status_code, 200)
        self.assertEqual(self.client.get(reverse("panel:storage")).status_code, 200)
