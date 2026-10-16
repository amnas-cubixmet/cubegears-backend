"""Free core modules require RBAC, but never a purchased feature entitlement."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.access import FREE_TENANT_MODULES
from apps.saas.feature_discovery import module_inventory
from apps.saas.models import FeatureGroup, PlanFeature
from apps.credits.models import (
    CompanyCreditFeature, CreditLedger, CreditWallet,
    FeatureBundle, FeatureCreditPrice, FeatureUsageSession,
)
from apps.credits.postpaid import feature_month_quote
from apps.credits.service import enable_paid_feature, renew_feature


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=True,
    CREDIT_STRICT_MODULE_GATING_ENABLED=True,
)
class FreeCoreModulesTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Free Workshop", slug="free-core-test", require_paid_features=True,
        )
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER",
            permissions=["dashboard.view", "settings.view", "settings.manage",
                         "staff.view", "staff.create", "staff.edit",
                         "staff.delete", "billing.view", "billing.edit",
                         "jobs.view", "stock.view"],
        )
        self.owner = User.objects.create_user(
            email="owner-free@example.test", name="Owner", password="VeryStrong123!",
            company=self.company, role=self.role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.staff_role = Role.objects.create(
            company=self.company, name="Front Desk", code="FRONT",
            permissions=["dashboard.view", "settings.view", "staff.view", "staff.create",
                         "jobs.view", "billing.view"],
        )
        self.staff_user = User.objects.create_user(
            email="employee-free@example.test", name="Employee",
            password="VeryStrong123!", company=self.company, role=self.staff_role,
        )
        self.group = FeatureGroup.objects.create(key="free-core-test-group", label="Core")
        self.free_feature = PlanFeature.objects.create(key="staff", label="Staff", group=self.group)
        self.paid_feature = PlanFeature.objects.create(key="jobs", label="Jobs", group=self.group)
        CreditWallet.objects.create(company=self.company, balance=Decimal("0.00"))
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_owner_can_open_all_free_modules_without_purchase(self):
        self.assertEqual(FREE_TENANT_MODULES, frozenset({"billing", "dashboard", "settings", "staff"}))
        for code in ("dashboard.view", "settings.view", "settings.manage", "staff.view", "staff.create",
                     "staff.edit", "staff.delete", "billing.view", "billing.edit"):
            self.assertTrue(user_has_permission(self.owner, code), code)
        self.assertFalse(user_has_permission(self.owner, "jobs.view"))
        self.assertFalse(user_has_permission(self.owner, "stock.view"))
        self.assertEqual(self.client.get("/api/v1/dashboard").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/employees").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/saas/credits/overview").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/jobs").status_code, 403)

    def test_staff_role_can_open_free_dashboard_and_staff_but_never_wallet(self):
        self.client.force_authenticate(self.staff_user)
        self.assertTrue(user_has_permission(self.staff_user, "staff.view"))
        self.assertTrue(user_has_permission(self.staff_user, "staff.create"))
        self.assertFalse(user_has_permission(self.staff_user, "staff.edit"))
        self.assertFalse(user_has_permission(self.staff_user, "billing.view"))
        self.assertEqual(self.client.get("/api/v1/dashboard").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/employees").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/saas/credits/overview").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/jobs").status_code, 403)

    def test_access_reports_free_permissions_separately_from_paid_features(self):
        r = self.client.get("/api/v1/saas/credits/access")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["freeModules"], ["billing", "dashboard", "settings", "staff"])
        self.assertEqual(r.data["activeLabelModules"], [])
        self.assertEqual(r.data["enabledModules"], [])
        self.assertIn("dashboard.view", r.data["permissions"])
        self.assertIn("staff.view", r.data["permissions"])
        entitlement = self.client.get("/api/v1/saas/credits/entitlements")
        self.assertEqual(entitlement.status_code, 200)
        self.assertEqual(entitlement.data["freeModules"], ["billing", "dashboard", "settings", "staff"])
        self.assertNotIn("staff", entitlement.data["modules"])
        self.assertNotIn("settings", entitlement.data["modules"])

    def test_role_removal_blocks_staff_despite_free_tier(self):
        self.staff_role.permissions = ["dashboard.view", "jobs.view"]
        self.staff_role.save(update_fields=["permissions"])
        self.client.force_authenticate(self.staff_user)
        self.assertFalse(user_has_permission(self.staff_user, "staff.view"))
        self.assertEqual(self.client.get("/api/v1/employees").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/dashboard").status_code, 200)

    def test_free_modules_are_not_sold_or_charged(self):
        discovered = {row["key"]: row for row in module_inventory()}
        self.assertFalse(discovered["staff"]["billable"])
        self.assertFalse(discovered["dashboard"]["billable"])
        self.assertFalse(discovered["settings"]["billable"])
        self.assertFalse(discovered["billing"]["billable"])
        self.assertTrue(discovered["jobs"]["billable"])

        free_bundle = FeatureBundle.objects.create(
            key="free-staff-test", name="Staff Only",
            price_credits=Decimal("0.00"), per_user_credits=Decimal("15.00"),
            billing_type="monthly",
        )
        free_bundle.features.set([self.free_feature])
        catalog = self.client.get("/api/v1/saas/credits/bundles")
        self.assertEqual(catalog.status_code, 200)
        # Do not advertise old free-only labels as paid products.
        self.assertFalse(any(row["id"] == free_bundle.pk for row in catalog.data))
        self.assertFalse(any(row["key"] in FREE_TENANT_MODULES for row in catalog.data))
        result = self.client.post("/api/v1/saas/credits/bundles/purchase",
                                  {"bundleId": free_bundle.pk}, format="json")
        self.assertEqual(result.status_code, 409)
        self.assertFalse(CreditLedger.objects.filter(company=self.company).exists())

    def test_old_standalone_free_charges_cannot_restart(self):
        pricing = FeatureCreditPrice.objects.create(
            feature=self.free_feature, base_credits=Decimal("8.00"),
        )
        with self.assertRaisesMessage(ValueError, "free"):
            enable_paid_feature(self.company, self.free_feature, self.owner)
        state = CompanyCreditFeature.objects.create(
            company=self.company, feature=self.free_feature, enabled=True,
            next_charge_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertEqual(renew_feature(state), "free_module")
        from apps.credits.service import price_for
        self.assertEqual(price_for(pricing, 1, 1), Decimal("0.00"))
        self.assertFalse(CreditLedger.objects.filter(company=self.company).exists())

    def test_historical_free_usage_is_excluded_from_postpaid_invoice(self):
        start = timezone.now() - timedelta(hours=2)
        month = timezone.localtime(start).strftime("%Y-%m")
        FeatureUsageSession.objects.create(
            company=self.company, feature=self.free_feature,
            started_at=start, ended_at=start + timedelta(hours=1),
            monthly_credits=Decimal("50.00"), activation_credits=Decimal("7.00"),
        )
        amount, rows = feature_month_quote(self.company, month)
        self.assertEqual(amount, Decimal("0.00"))
        self.assertEqual(rows, [])

    def test_second_tenant_cannot_read_company_staff(self):
        other = Company.objects.create(name="Other Workshop", slug="free-other-workshop")
        role = Role.objects.create(
            company=other, name="Employee", code="EMP",
            permissions=["dashboard.view", "staff.view"],
        )
        outsider = User.objects.create_user(
            email="other-free@example.test", name="Other",
            password="VeryStrong123!", company=other, role=role,
        )
        self.client.force_authenticate(outsider)
        result = self.client.get("/api/v1/employees")
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("Owner", str(result.data))
