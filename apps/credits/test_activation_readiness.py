"""End-to-end activation readiness: no false button state or charges."""
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, PlanFeature

from .models import CreditLedger, CreditWallet, FeatureBundle, FeatureCreditPrice


class ActivationReadinessTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Activation Garage", slug="activation-garage")
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER",
            permissions=["billing.view", "billing.edit", "jobs.view", "customers.view"],
        )
        self.user = User.objects.create_user(
            email="activation-owner@example.test", name="Owner",
            password="TestActivationPass123!", company=self.company, role=self.role,
        )
        self.user.company.owner = self.user
        self.user.company.save(update_fields=["owner"])
        self.group = FeatureGroup.objects.create(key="activation-group", label="Operations")
        self.jobs = PlanFeature.objects.create(key="jobs", label="Jobs", group=self.group)
        self.customers = PlanFeature.objects.create(key="customers", label="Customers", group=self.group)
        self.bundle = FeatureBundle.objects.create(
            key="starter-tools", name="Starter Tools", billing_type="weekly",
            price_credits=Decimal("23.00"), per_user_credits=Decimal("23.00"),
        )
        self.bundle.features.set([self.jobs, self.customers])
        FeatureCreditPrice.objects.create(
            feature=self.jobs, base_credits=Decimal("10.00"),
        )
        self.wallet = CreditWallet.objects.create(company=self.company, balance=Decimal("100.00"))
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False)
    def test_platform_disabled_blocks_both_paid_activation_paths_without_charges(self):
        bundles = self.client.get("/api/v1/saas/credits/bundles")
        self.assertEqual(bundles.status_code, 200)
        self.assertFalse(bundles.data[0]["purchaseEnabled"])
        self.assertEqual(bundles.data[0]["purchaseDisabledReason"], "FEATURE_ENTITLEMENTS_NOT_ENABLED")
        prices = self.client.get("/api/v1/saas/credits/features")
        self.assertEqual(prices.status_code, 200)
        self.assertFalse(prices.data[0]["activationEnabled"])

        for path, payload in [
            ("/api/v1/saas/credits/bundles/purchase", {"bundleId": self.bundle.pk}),
            ("/api/v1/saas/credits/features/enable", {"key": "jobs"}),
        ]:
            response = self.client.post(path, payload, format="json")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data["code"], "FEATURE_ENTITLEMENTS_NOT_ENABLED")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        self.assertFalse(CreditLedger.objects.exists())

    @override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=True)
    def test_enforced_bundle_purchase_enables_access_and_charges_server_price(self):
        listing = self.client.get("/api/v1/saas/credits/bundles").data
        self.assertTrue(listing[0]["purchaseEnabled"])
        self.assertIsNone(listing[0]["purchaseDisabledReason"])
        self.assertFalse(user_has_permission(self.user, "jobs.view"))
        purchase = self.client.post(
            "/api/v1/saas/credits/bundles/purchase", {"bundleId": self.bundle.pk}, format="json"
        )
        self.assertEqual(purchase.status_code, 200)
        self.assertEqual(purchase.data["chargedCredits"], "23.00")
        self.assertTrue(user_has_permission(self.user, "jobs.view"))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("77.00"))
        self.assertEqual(CreditLedger.objects.filter(kind="bundle_charge").count(), 1)

        disabled = self.client.post(
            "/api/v1/saas/credits/bundles/disable", {"bundleId": self.bundle.pk}, format="json"
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(user_has_permission(self.user, "jobs.view"))

    @override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=True)
    def test_per_use_or_empty_label_cannot_be_purchased(self):
        self.bundle.billing_type = "per_use"
        self.bundle.save(update_fields=["billing_type"])
        listing = self.client.get("/api/v1/saas/credits/bundles").data
        self.assertEqual(listing[0]["purchaseDisabledReason"], "METERED_OPERATION_NOT_READY")
        self.assertFalse(listing[0]["purchaseEnabled"])

        self.bundle.billing_type = "weekly"
        self.bundle.save(update_fields=["billing_type"])
        self.jobs.is_active = False
        self.jobs.save(update_fields=["is_active"])
        self.customers.is_active = False
        self.customers.save(update_fields=["is_active"])
        listing = self.client.get("/api/v1/saas/credits/bundles").data
        self.assertEqual(listing[0]["purchaseDisabledReason"], "FEATURE_LABEL_EMPTY")
        self.assertFalse(listing[0]["purchaseEnabled"])
