"""Feature visibility must match backend RBAC and purchased entitlement."""
from decimal import Decimal
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, PlanFeature
from apps.credits.models import CreditWallet, FeatureCreditPrice
from apps.accounts.permissions import user_has_permission


class ModuleVisibilityTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="UI Access Garage", slug="ui-access-garage")
        self.role = Role.objects.create(company=self.company, name="Operator", code="OPERATOR",
                                        permissions=["dashboard.view", "billing.view", "billing.edit",
                                                     "jobs.view", "stock.view"])
        self.user = User.objects.create_user(email="module-visibility@example.test",
                    name="Module User", password="ModuleSecurePass123!",
                    company=self.company, role=self.role)
        self.group = FeatureGroup.objects.create(key="visibility-group", label="Visibility")
        self.jobs = PlanFeature.objects.create(key="jobs", label="Jobs", group=self.group)
        self.stock = PlanFeature.objects.create(key="stock", label="Stock", group=self.group)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
                       CREDIT_STRICT_MODULE_GATING_ENABLED=False)
    def test_unenforced_payload_keeps_existing_company_working(self):
        r = self.client.get("/api/v1/saas/credits/entitlements")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["enforced"])
        self.assertIn("jobs", r.data["modules"])
        self.assertIn("stock", r.data["modules"])

    @override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
                       CREDIT_STRICT_MODULE_GATING_ENABLED=True)
    def test_strict_mode_hides_all_unpurchased_modules(self):
        r = self.client.get("/api/v1/saas/credits/entitlements")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["enforced"])
        self.assertEqual(r.data["modules"], [])
        self.assertFalse(user_has_permission(self.user, "jobs.view"))
        self.assertTrue(user_has_permission(self.user, "billing.view"))

    @override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
                       CREDIT_STRICT_MODULE_GATING_ENABLED=True)
    def test_paid_feature_enabled_only_for_own_company_role(self):
        from django.utils import timezone
        from datetime import timedelta
        from apps.credits.models import CompanyCreditFeature
        CompanyCreditFeature.objects.create(company=self.company, feature=self.jobs,
                       enabled=True,next_charge_at=timezone.now()+timedelta(days=1))
        r=self.client.get("/api/v1/saas/credits/entitlements")
        self.assertEqual(r.data["modules"], ["jobs"])
        self.assertTrue(user_has_permission(self.user, "jobs.view"))
        self.assertFalse(user_has_permission(self.user, "stock.view"))
        other=Company.objects.create(name="Other",slug="visibility-other")
        other_role=Role.objects.create(company=other,name="Other",code="ADMIN",
                         permissions=["jobs.view","billing.view"])
        other_user=User.objects.create_user(email="other-module@example.test",name="Other User",
                      password="ModuleSecurePass123!",company=other,role=other_role)
        self.client.force_authenticate(other_user)
        self.assertEqual(self.client.get("/api/v1/saas/credits/entitlements").data["modules"], [])

    def test_anonymous_user_cannot_read_module_data(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get("/api/v1/saas/credits/entitlements").status_code,401)
