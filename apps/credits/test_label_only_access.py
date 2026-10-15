"""The purchased active label is the only paid workshop entitlement in opt-in mode."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, PlanFeature
from apps.credits.models import (
    CompanyBundleAccess, CompanyCreditFeature, CreditWallet, FeatureBundle,
)


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=True,
    CREDIT_STRICT_MODULE_GATING_ENABLED=False,
)
class LabelOnlyAccessTests(TestCase):
    def setUp(self):
        self.a = Company.objects.create(name="Label Garage A", slug="label-garage-a")
        self.b = Company.objects.create(name="Label Garage B", slug="label-garage-b")
        self.role_a = Role.objects.create(
            company=self.a, name="Garage Admin", code="ADMIN",
            permissions=["dashboard.view", "billing.view", "billing.edit",
                         "jobs.view", "customers.view", "stock.view", "invoices.view"],
        )
        self.role_b = Role.objects.create(
            company=self.b, name="Other Admin", code="ADMIN",
            permissions=["dashboard.view", "billing.view", "jobs.view"],
        )
        self.owner = User.objects.create_user(
            email="label-a@example.test", name="Owner A",
            password="LabelAccessSecure123!", company=self.a, role=self.role_a,
        )
        self.owner.company.owner = self.owner
        self.owner.company.save(update_fields=["owner"])
        self.other = User.objects.create_user(
            email="label-b@example.test", name="Owner B",
            password="LabelAccessSecure123!", company=self.b, role=self.role_b,
        )
        self.other.company.owner = self.other
        self.other.company.save(update_fields=["owner"])
        group = FeatureGroup.objects.create(key="label-test-group", label="Workshop")
        self.jobs = PlanFeature.objects.create(key="jobs", label="Jobs", group=group)
        self.customers = PlanFeature.objects.create(key="customers", label="Customers", group=group)
        self.stock = PlanFeature.objects.create(key="stock", label="Stock", group=group)
        self.label = FeatureBundle.objects.create(
            key="job-customer-label", name="Job and Customer",
            price_credits=Decimal("0"), per_user_credits=Decimal("10.00"),
            billing_type="weekly",
        )
        self.label.features.set([self.jobs, self.customers])
        CreditWallet.objects.create(company=self.a, balance=Decimal("100.00"))
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_before_purchase_only_core_permissions_are_available(self):
        self.assertTrue(user_has_permission(self.owner, "billing.view"))
        self.assertTrue(user_has_permission(self.owner, "dashboard.view"))
        self.assertFalse(user_has_permission(self.owner, "jobs.view"))
        self.assertFalse(user_has_permission(self.owner, "stock.view"))
        r = self.client.get("/api/v1/saas/credits/access")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["labelOnly"])
        self.assertEqual(r.data["activeLabelModules"], [])
        self.assertEqual(r.data["enabledModules"], [])
        self.assertNotIn("jobs.view", r.data["permissions"])
        self.assertEqual(self.client.get("/api/v1/jobs").status_code, 403)

    def test_purchased_label_unlocks_included_modules_only_for_own_company(self):
        activation = self.client.post(
            "/api/v1/saas/credits/bundles/purchase",
            {"bundleId": self.label.pk}, format="json",
        )
        self.assertEqual(activation.status_code, 200)
        self.assertEqual(activation.data["chargedCredits"], "10.00")
        r = self.client.get("/api/v1/saas/credits/access")
        self.assertEqual(r.data["activeLabelModules"], ["customers", "jobs"])
        self.assertEqual(r.data["enabledModules"], ["customers", "jobs"])
        self.assertIn("jobs.view", r.data["permissions"])
        self.assertNotIn("stock.view", r.data["permissions"])
        self.assertTrue(user_has_permission(self.owner, "jobs.view"))
        self.assertFalse(user_has_permission(self.owner, "stock.view"))
        self.assertEqual(self.client.get("/api/v1/jobs").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/stock/items").status_code, 403)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get("/api/v1/saas/credits/access").data["enabledModules"], [])
        self.assertEqual(self.client.get("/api/v1/jobs").status_code, 403)

    def test_single_feature_purchase_does_not_unlock_unlabeled_module(self):
        from apps.credits.models import FeatureCreditPrice
        FeatureCreditPrice.objects.create(feature=self.stock, base_credits=Decimal("1"))
        CompanyCreditFeature.objects.create(
            company=self.a, feature=self.stock, enabled=True,
            next_charge_at=timezone.now() + timedelta(days=2),
        )
        self.assertFalse(user_has_permission(self.owner, "stock.view"))
        self.assertEqual(self.client.get("/api/v1/stock/items").status_code, 403)

    def test_disabling_and_expiring_label_revokes_routes_immediately(self):
        access = CompanyBundleAccess.objects.create(
            company=self.a, bundle=self.label, enabled=True,
            valid_until=timezone.now() + timedelta(minutes=5),
            billing_type_at_purchase="weekly",
        )
        self.assertTrue(user_has_permission(self.owner, "jobs.view"))
        self.assertIsNotNone(self.client.get("/api/v1/saas/credits/access").data["nextLabelExpiryAt"])
        access.valid_until = timezone.now() - timedelta(seconds=1)
        access.save(update_fields=["valid_until"])
        self.assertFalse(user_has_permission(self.owner, "jobs.view"))
        access.enabled = True
        access.valid_until = timezone.now() + timedelta(days=1)
        access.save(update_fields=["enabled", "valid_until"])
        r = self.client.post("/api/v1/saas/credits/bundles/disable", {"bundleId": self.label.id}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/saas/credits/access").data["activeLabelModules"], [])
        self.assertFalse(user_has_permission(self.owner, "jobs.view"))

    def test_role_without_permission_cannot_use_purchased_label(self):
        CompanyBundleAccess.objects.create(
            company=self.a, bundle=self.label, enabled=True,
            valid_until=timezone.now() + timedelta(days=1),
            billing_type_at_purchase="weekly",
        )
        staff_role = Role.objects.create(
            company=self.a, name="Front desk", code="FRONT",
            permissions=["customers.view", "billing.view"],
        )
        staff = User.objects.create_user(
            email="label-staff@example.test", name="Front",
            password="LabelAccessSecure123!", company=self.a, role=staff_role,
        )
        self.client.force_authenticate(staff)
        r = self.client.get("/api/v1/saas/credits/access")
        self.assertEqual(r.data["activeLabelModules"], ["customers", "jobs"])
        self.assertEqual(r.data["enabledModules"], ["customers"])
        self.assertFalse(user_has_permission(staff, "jobs.view"))

    @override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False)
    def test_misconfigured_label_mode_fails_closed_and_keeps_wallet_available(self):
        self.assertFalse(user_has_permission(self.owner, "jobs.view"))
        self.assertTrue(user_has_permission(self.owner, "billing.view"))
        r = self.client.get("/api/v1/saas/credits/access")
        self.assertTrue(r.data["enforcementEnabled"])
        self.assertEqual(r.data["enabledModules"], [])
