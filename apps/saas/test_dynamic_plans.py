from decimal import Decimal
from django.test import TestCase, override_settings
from django.utils import timezone
from datetime import timedelta
from apps.credits.models import CompanyCreditFeature
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.roles.models import Role
from apps.accounts.permissions import user_has_permission
from .models import (
    Subscription, SubscriptionPlan, FeatureGroup, PlanFeature,
    PlanFeatureAccess, PlanChangeRequest, CustomPlanRequest,
)


class DynamicSaasPlanTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Plan Test Garage", slug="plan-test-garage")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER", permissions=["*"], is_active=True
        )
        self.user = User.objects.create_user(
            email="owner@plan-tests.example", password="example-pass-2026",
            company=self.company, branch=self.branch, role=self.role, name="Owner",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.group = FeatureGroup.objects.create(key="operations", label="Operations")
        self.feature = PlanFeature.objects.create(key="customers", label="Customers", group=self.group)
        self.plan = SubscriptionPlan.objects.create(
            key="professional", name="Professional",
            monthly_price=Decimal("1499.00"), yearly_price=Decimal("14990"),
            max_branches=1, max_users=1, included_storage_gb=20,
            extra_storage_rate=Decimal("15.00"),
        )

    def test_plan_catalog_includes_dynamic_feature_labels_and_prices(self):
        PlanFeatureAccess.objects.create(plan=self.plan, feature=self.feature, enabled=True)
        result = self.client.get("/api/v1/saas/plans")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data[0]["monthlyPrice"], "1499.00")
        self.assertEqual(result.data[0]["maxUsers"], 1)
        self.assertEqual(result.data[0]["features"][0]["group"], "Operations")
        self.assertTrue(result.data[0]["features"][0]["enabled"])

    def test_plan_change_is_request_only_not_paid_subscription(self):
        result = self.client.post(
            "/api/v1/saas/billing/change-requests",
            {"plan_id": self.plan.id, "billing_cycle": "yearly"}, format="json"
        )
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(PlanChangeRequest.objects.get().status, "pending")
        self.assertFalse(Subscription.objects.filter(company=self.company).exists())

    def test_tenant_cannot_write_subscription_or_usage(self):
        response = self.client.post(
            "/api/v1/saas/billing/subscriptions",
            {"plan": "Unlimited", "amount": 0, "status": "Active"}, format="json"
        )
        self.assertEqual(response.status_code, 405, response.data)
        response = self.client.post(
            "/api/v1/saas/storage/history-records",
            {"date": "2026-10-14", "bytes_used": 0}, format="json"
        )
        self.assertEqual(response.status_code, 405, response.data)

    @override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=True)
    def test_credit_feature_switch_never_overrides_role_permission(self):
        # A paid module must be active as well as permitted by the user's role.
        self.assertFalse(user_has_permission(self.user, "customers.view"))
        self.assertTrue(user_has_permission(self.user, "invoices.view"))
        CompanyCreditFeature.objects.create(
            company=self.company, feature=self.feature, enabled=True,
            next_charge_at=timezone.now() + timedelta(days=30),
        )
        fresh_user = User.objects.get(pk=self.user.pk)
        self.assertTrue(user_has_permission(fresh_user, "customers.view"))
        fresh_user.role.permissions = ["invoices.view"]
        fresh_user.role.save(update_fields=["permissions"])
        fresh_user = User.objects.get(pk=self.user.pk)
        self.assertFalse(user_has_permission(fresh_user, "customers.view"))

    def test_active_plan_enforces_branch_and_login_user_capacity(self):
        Subscription.objects.create(
            company=self.company, branch=self.branch, plan="Professional",
            status="Active", plan_definition=self.plan, seats=1,
        )
        response = self.client.post(
            "/api/v1/branches/", {"name": "Second Branch", "code": "SEC"}, format="json"
        )
        self.assertEqual(response.status_code, 400, response.data)

    def test_custom_request_is_company_scoped(self):
        result = self.client.post(
            "/api/v1/saas/billing/custom-requests",
            {"email": "contact@plan-tests.example", "branches": 2, "users": 15,
             "message": "Need extra shops and warehouse stock."}, format="json"
        )
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(CustomPlanRequest.objects.get().company_id, self.company.pk)
        self.assertEqual(self.client.get("/api/v1/saas/billing/custom-requests").data[0]["status"], "pending")
