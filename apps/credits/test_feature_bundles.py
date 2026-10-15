"""Paid labels are tenant-scoped, server-priced and never bypass role RBAC."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, PlanFeature
from .models import CreditWallet, CreditLedger, FeatureBundle, CompanyBundleAccess
from .service import consume_bundle_use


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=True)
class FeatureBundlesAPITests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Garage One", slug="feature-bundle-garage-one")
        self.other = Company.objects.create(name="Garage Two", slug="feature-bundle-garage-two")
        admin_role = Role.objects.create(
            company=self.company, name="Billing Admin", code="ADMIN",
            permissions=["billing.view", "billing.edit", "jobs.view", "invoices.view"],
        )
        self.admin = User.objects.create_user(
            email="bundle-admin@example.test", name="Billing Admin",
            password="SecureBundlePass123!", company=self.company, role=admin_role,
        )
        role = Role.objects.create(
            company=self.company, name="Basic Staff", code="STAFF",
            permissions=["jobs.view"],
        )
        self.staff = User.objects.create_user(
            email="bundle-staff@example.test", name="Workshop Staff",
            password="SecureBundlePass123!", company=self.company, role=role,
        )
        self.group = FeatureGroup.objects.create(key="bundle-ops", label="Operations")
        self.jobs = PlanFeature.objects.create(key="jobs", label="Job Cards", group=self.group)
        self.invoices = PlanFeature.objects.create(key="invoices", label="Invoices", group=self.group)
        self.bundle = FeatureBundle.objects.create(
            key="garage-plus", name="Garage Plus", price_credits=Decimal("40.00"),
            billing_type="weekly",
        )
        self.bundle.features.set([self.jobs, self.invoices])
        CreditWallet.objects.create(company=self.company, balance=Decimal("100.00"))
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_company_sees_label_and_cannot_choose_price(self):
        response = self.client.get("/api/v1/saas/credits/bundles")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["priceCredits"], "40.00")
        self.assertEqual({f["key"] for f in response.data[0]["features"]}, {"jobs", "invoices"})
        self.assertFalse(response.data[0]["active"])
        result = self.client.post("/api/v1/saas/credits/bundles/purchase", {
            "bundleId": self.bundle.pk, "priceCredits": "0.00", "features": [],
        }, format="json")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["chargedCredits"], "40.00")
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("60.00"))
        self.assertEqual(CreditLedger.objects.filter(company=self.company, kind="bundle_charge").count(), 1)
        access = CompanyBundleAccess.objects.get(company=self.company, bundle=self.bundle)
        self.assertGreater(access.valid_until, timezone.now() + timedelta(days=6))
        self.assertTrue(user_has_permission(self.admin, "jobs.view"))
        self.assertTrue(user_has_permission(self.admin, "invoices.view"))

    def test_purchase_does_not_grant_missing_role_permission(self):
        self.client.post("/api/v1/saas/credits/bundles/purchase", {
            "bundleId": self.bundle.pk,
        }, format="json")
        self.assertTrue(user_has_permission(self.staff, "jobs.view"))
        self.assertFalse(user_has_permission(self.staff, "invoices.view"))

    def test_other_company_cannot_use_purchased_label(self):
        self.client.post("/api/v1/saas/credits/bundles/purchase", {
            "bundleId": self.bundle.pk,
        }, format="json")
        other_role = Role.objects.create(
            company=self.other, name="Other Admin", code="ADMIN",
            permissions=["billing.edit", "jobs.view"],
        )
        other_admin = User.objects.create_user(
            email="bundle-other@example.test", name="Other Admin",
            password="SecureBundlePass123!", company=self.other, role=other_role,
        )
        self.assertFalse(user_has_permission(other_admin, "jobs.view"))
        other_client = APIClient()
        other_client.force_authenticate(other_admin)
        list_response = other_client.get("/api/v1/saas/credits/bundles")
        self.assertEqual(list_response.status_code, 200)
        self.assertFalse(list_response.data[0]["active"])

    def test_staff_cannot_purchase_bundles(self):
        self.client.force_authenticate(self.staff)
        result = self.client.post("/api/v1/saas/credits/bundles/purchase", {
            "bundleId": self.bundle.pk,
        }, format="json")
        self.assertEqual(result.status_code, 403)
        self.assertEqual(CreditLedger.objects.count(), 0)

    def test_weekly_extensions_and_expiry_are_enforced(self):
        for _ in range(2):
            self.assertEqual(self.client.post("/api/v1/saas/credits/bundles/purchase", {
                "bundleId": self.bundle.pk,
            }, format="json").status_code, 200)
        access = CompanyBundleAccess.objects.get(company=self.company, bundle=self.bundle)
        self.assertGreater(access.valid_until, timezone.now() + timedelta(days=13))
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("20.00"))
        access.valid_until = timezone.now() - timedelta(minutes=1)
        access.save(update_fields=["valid_until"])
        self.assertFalse(user_has_permission(self.admin, "jobs.view"))

    def test_hourly_daily_and_permanent_periods(self):
        self.bundle.billing_type = "hourly"
        self.bundle.save(update_fields=["billing_type"])
        self.assertEqual(self.client.post("/api/v1/saas/credits/bundles/purchase", {
            "bundleId": self.bundle.pk,
        }, format="json").status_code, 200)
        access = CompanyBundleAccess.objects.get(company=self.company, bundle=self.bundle)
        self.assertTrue(timezone.now() + timedelta(minutes=59) < access.valid_until)
        self.bundle.billing_type = "daily"
        self.bundle.save(update_fields=["billing_type"])
        self.assertEqual(self.client.post("/api/v1/saas/credits/bundles/purchase", {
            "bundleId": self.bundle.pk,
        }, format="json").status_code, 200)
        access.refresh_from_db()
        self.assertGreater(access.valid_until, timezone.now() + timedelta(hours=23))

    def test_permanent_label_is_charged_only_once(self):
        self.bundle.billing_type = "permanent"
        self.bundle.save(update_fields=["billing_type"])
        first = self.client.post("/api/v1/saas/credits/bundles/purchase", {
            "bundleId": self.bundle.pk,
        }, format="json")
        second = self.client.post("/api/v1/saas/credits/bundles/purchase", {
            "bundleId": self.bundle.pk,
        }, format="json")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.data["chargedCredits"], "0.00")
        self.assertEqual(CreditLedger.objects.filter(kind="bundle_charge").count(), 1)
        access = CompanyBundleAccess.objects.get(company=self.company, bundle=self.bundle)
        self.assertIsNone(access.valid_until)

    def test_insufficient_credit_does_not_grant_any_access(self):
        CreditWallet.objects.filter(company=self.company).update(balance=Decimal("1.00"))
        result = self.client.post("/api/v1/saas/credits/bundles/purchase", {
            "bundleId": self.bundle.pk,
        }, format="json")
        self.assertEqual(result.status_code, 409)
        self.assertEqual(CompanyBundleAccess.objects.count(), 0)
        self.assertEqual(CreditLedger.objects.count(), 0)

    def test_disable_does_not_refund(self):
        self.client.post("/api/v1/saas/credits/bundles/purchase", {
            "bundleId": self.bundle.pk,
        }, format="json")
        result = self.client.post("/api/v1/saas/credits/bundles/disable", {
            "bundleId": self.bundle.pk,
        }, format="json")
        self.assertEqual(result.status_code, 200)
        self.assertFalse(user_has_permission(self.admin, "jobs.view"))
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("60.00"))

    def test_per_use_not_sold_before_server_operation_is_connected(self):
        self.bundle.billing_type = "per_use"
        self.bundle.save(update_fields=["billing_type"])
        result = self.client.post("/api/v1/saas/credits/bundles/purchase", {
            "bundleId": self.bundle.pk,
        }, format="json")
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.data["code"], "METERED_OPERATION_NOT_READY")
        self.assertEqual(CreditLedger.objects.count(), 0)

    def test_server_side_usage_hook_consumes_once_and_is_idempotent(self):
        self.bundle.billing_type = "per_use"
        self.bundle.save(update_fields=["billing_type"])
        CompanyBundleAccess.objects.create(
            company=self.company, bundle=self.bundle, enabled=True, uses_remaining=1,
        )
        self.assertTrue(consume_bundle_use(
            self.company, self.bundle, self.jobs, operation_ref="real-job-7", actor=self.admin
        ))
        self.assertFalse(consume_bundle_use(
            self.company, self.bundle, self.jobs, operation_ref="real-job-7", actor=self.admin
        ))
        access = CompanyBundleAccess.objects.get(company=self.company, bundle=self.bundle)
        self.assertEqual(access.uses_remaining, 0)
        self.assertFalse(access.enabled)
        self.assertEqual(CreditLedger.objects.filter(kind="bundle_use").count(), 1)


class FeatureBundlePanelUITests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="feature-bundle-panel@example.test", name="Platform Admin",
            password="SecureBundlePanel123!",
        )
        self.client.force_login(self.admin)
        group = FeatureGroup.objects.create(key="panel-bundle", label="Operations")
        self.a = PlanFeature.objects.create(key="panel-bundle-jobs", label="Job Cards", group=group)
        self.b = PlanFeature.objects.create(key="panel-bundle-invoices", label="Invoices", group=group)

    def test_platform_admin_can_create_label_from_two_existing_features(self):
        url = reverse("panel:featurebundle-create")
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="features"')
        self.assertContains(page, 'name="billing_type"')
        result = self.client.post(url, {
            "name": "Operations Bundle", "description": "Two modules",
            "features": [str(self.a.pk), str(self.b.pk)],
            "price_credits": "35.00", "billing_type": "daily",
            "is_active": "on", "sort_order": "2",
        })
        self.assertRedirects(result, reverse("panel:features"))
        bundle = FeatureBundle.objects.get(key="operations-bundle")
        self.assertEqual(bundle.features.count(), 2)
        self.assertEqual(bundle.price_credits, Decimal("35.00"))
        page = self.client.get(reverse("panel:features"))
        self.assertContains(page, "Operations Bundle")
        self.assertContains(page, "35.00")

    def test_label_requires_multiple_existing_features(self):
        result = self.client.post(reverse("panel:featurebundle-create"), {
            "name": "Invalid Bundle", "features": [str(self.a.pk)],
            "price_credits": "5.00", "billing_type": "weekly", "is_active": "on",
        })
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Select at least two existing features")
        self.assertFalse(FeatureBundle.objects.filter(name="Invalid Bundle").exists())
