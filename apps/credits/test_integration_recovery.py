"""Regression tests for the two independent metering schemas and safe defaults."""
from decimal import Decimal

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.credits.models import (
    CompanyPostpaidFeature, CreditWallet, FeatureCreditPrice,
    FeatureMeterPeriod, StorageCreditPolicy, StorageMeterCursor,
    StorageMeterPeriod, StorageMeterReading, FeatureMeterReading,
)
from apps.credits.monthly_storage import _policy
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, PlanFeature


class BillingModelIntegrationTests(TestCase):
    def test_monthly_models_are_registered_and_mapped(self):
        from django.apps import apps
        for name in (
            "CompanyPostpaidFeature", "FeatureMeterPeriod", "FeatureMeterReading",
            "StorageMeterCursor", "StorageMeterPeriod", "StorageMeterReading",
        ):
            self.assertIsNotNone(apps.get_model("credits", name))

    @override_settings(
        CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
        CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=True,
        CREDIT_POSTPAID_FEATURE_BILLING_ENABLED=False,
    )
    def test_company_wallet_and_feature_list_have_defined_monthly_fields(self):
        company = Company.objects.create(name="Fix Billing", slug="fix-billing-tests")
        role = Role.objects.create(
            company=company, name="Billing Admin", code="BILLING_ADMIN",
            permissions=["billing.view", "billing.edit", "jobs.view"],
        )
        owner = User.objects.create_user(
            company=company, role=role, name="Billing Owner",
            email="billing-fix@example.test", password="BillingFixStrong123!",
        )
        group = FeatureGroup.objects.create(key="fix-billing", label="Fix Billing")
        feature = PlanFeature.objects.create(
            key="jobs", group=group, label="Job Cards",
        )
        FeatureCreditPrice.objects.create(
            feature=feature, billing_period="monthly",
            base_credits=Decimal("10.00"),
        )
        CreditWallet.objects.create(company=company, balance=Decimal("50.00"))
        client = APIClient()
        client.force_authenticate(user=owner)
        before = client.get("/api/v1/saas/credits/overview")
        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.data["activeFeatures"], 0)
        self.assertEqual(before.data["postpaidAccruedCredits"], "0.00")
        enabled = client.post(
            "/api/v1/saas/credits/features/enable",
            {"key": feature.key}, format="json",
        )
        self.assertEqual(enabled.status_code, 200)
        self.assertEqual(enabled.data["billingMode"], "monthly_postpaid")
        active = client.get("/api/v1/saas/credits/overview")
        self.assertEqual(active.status_code, 200)
        self.assertEqual(active.data["activeFeatures"], 1)
        self.assertEqual(active.data["balance"], "50.00")
        listed = client.get("/api/v1/saas/credits/features")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data[0]["billingMode"], "monthly_postpaid")
        self.assertTrue(listed.data[0]["enabled"])
        self.assertFalse(listed.data[0]["suspended"])
        self.assertEqual(CompanyPostpaidFeature.objects.filter(company=company).count(), 1)

    @override_settings(STORAGE_MONTHLY_METERED_ENABLED=True)
    def test_new_storage_engine_rejects_legacy_metered_policy(self):
        StorageCreditPolicy.objects.create(
            is_active=True, billing_mode="metered_monthly",
            billing_period="monthly", credits_per_gb=Decimal("10.00"),
        )
        with self.assertRaisesRegex(ValueError, "Legacy monthly storage"):
            _policy()

    @override_settings(STORAGE_MONTHLY_METERED_ENABLED=True)
    def test_new_storage_engine_uses_only_snapshot_policy_in_new_mode(self):
        policy = StorageCreditPolicy.objects.create(
            is_active=True, billing_mode="snapshot",
            billing_period="monthly", credits_per_gb=Decimal("10.00"),
        )
        self.assertEqual(_policy().pk, policy.pk)


class BillingFlagTests(SimpleTestCase):
    def test_legacy_charging_tasks_do_not_execute_without_optin(self):
        from django.conf import settings
        try:
            from apps.credits import tasks
        except ImportError as exc:
            if exc.name == "celery":
                self.skipTest("Optional Celery package is not installed in web-only CI.")
            raise
        with override_settings(
            CREDIT_BILLING_CELERY_ENABLED=False,
            STORAGE_MONTHLY_METERED_ENABLED=False,
            CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=False,
        ):
            self.assertEqual(tasks.sample_monthly_storage(), {"disabled": 1})
            self.assertEqual(tasks.settle_monthly_storage(), {"disabled": 1})
            self.assertEqual(tasks.sample_monthly_features(), {"disabled": 1})
            self.assertEqual(tasks.settle_monthly_features(), {"disabled": 1})
        self.assertIsInstance(settings.CREDIT_FEATURE_STRICT_GATE_ENABLED, bool)
