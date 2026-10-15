"""Real monthly feature/storage credit cycles, legacy safety and RBAC."""
from datetime import datetime
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.credits.models import (
    CompanyPostpaidFeature, CreditLedger, CreditWallet,
    FeatureCreditPrice, FeatureMeterPeriod, FeatureMeterReading,
    StorageCreditPolicy, StorageMeterPeriod, StorageMeterReading,
)
from apps.credits.monthly_features import (
    activate_postpaid_feature, deactivate_postpaid_feature, meter_postpaid_feature,
    settle_postpaid_feature,
)
from apps.credits.monthly_storage import meter_company_storage, settle_company_storage
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, MediaFile, PlanFeature


TZ = timezone.get_default_timezone()


def clock(year, month, day, hour=0, minute=0):
    return timezone.make_aware(datetime(year, month, day, hour, minute), TZ)


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
    CREDIT_FEATURE_STRICT_GATE_ENABLED=True,
    CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=True,
    STORAGE_MONTHLY_METERED_ENABLED=True,
    STORAGE_METER_INTERVAL_SECONDS=35 * 86400,  # deterministic full-month simulated cycle
)
class MonthlyMeteredBillingTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Metered Billing Garage", slug="metered-billing-garage")
        self.other_company = Company.objects.create(name="Other Garage", slug="other-monthly-meter")
        self.role = Role.objects.create(
            company=self.company, name="Billing Owner", code="BILLING_OWNER",
            permissions=["billing.view", "billing.edit", "dashboard.view", "jobs.view"],
        )
        self.user = User.objects.create_user(
            company=self.company, role=self.role,
            email="monthly-meter-owner@example.test", name="Monthly Owner",
            password="StrongMonthlyTests123!",
        )
        self.user.company.owner = self.user
        self.user.company.save(update_fields=["owner"])
        self.member = User.objects.create_user(
            company=self.company, role=self.role,
            email="monthly-meter-staff@example.test", name="Staff",
            password="StrongMonthlyTests123!",
        )
        self.group = FeatureGroup.objects.create(key="monthly-test", label="Paid Workshop Features")
        self.feature = PlanFeature.objects.create(key="jobs", label="Job Cards", group=self.group)
        FeatureCreditPrice.objects.create(
            feature=self.feature, base_credits=Decimal("10.00"),
            per_user_credits=Decimal("5.00"),
            activation_credits=Decimal("2.00"),
            billing_period="monthly",
        )
        CreditWallet.objects.create(company=self.company, balance=Decimal("100.00"))

    def test_backend_access_is_role_and_entitlement_based(self):
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.user)
        url = "/api/v1/saas/credits/access"
        initial = self.api_client.get(url)
        self.assertEqual(initial.status_code, 200)
        self.assertNotIn("jobs.view", initial.data["permissions"])
        self.assertNotIn("jobs", initial.data["enabledModules"])
        self.assertIn("billing.view", initial.data["permissions"])
        self.assertFalse(user_has_permission(self.member, "jobs.view"))

        state, upfront = activate_postpaid_feature(self.company, self.feature)
        self.assertEqual(upfront, Decimal("0"))
        self.assertTrue(user_has_permission(self.user, "jobs.view"))
        self.assertTrue(user_has_permission(self.member, "jobs.view"))
        activated = self.api_client.get(url)
        self.assertIn("jobs", activated.data["enabledModules"])
        self.assertIn("jobs.view", activated.data["permissions"])
        deactivate_postpaid_feature(self.company, self.feature)
        self.assertFalse(user_has_permission(self.member, "jobs.view"))

    def test_monthly_postpaid_fee_exactly_once_and_no_immediate_debit(self):
        state, upfront = activate_postpaid_feature(self.company, self.feature, at=clock(2026, 9, 1))
        self.assertEqual(upfront, Decimal("0"))
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("100.00"))
        result, reading = meter_postpaid_feature(state, at=clock(2026, 10, 1))
        self.assertEqual(result, "metered")
        self.assertGreater(reading.covered_seconds, 0)
        month = FeatureMeterPeriod.objects.get(company=self.company, feature=self.feature, period="2026-09")
        self.assertEqual(month.accrued_credits.quantize(Decimal("0.01")), Decimal("20.00"))
        self.assertEqual(month.activation_credits, Decimal("2.00"))
        self.assertGreater(FeatureMeterReading.objects.filter(company=self.company).count(), 1)

        first, amount = settle_postpaid_feature(self.company, self.feature, "2026-09", at=clock(2026, 10, 2))
        second, _ = settle_postpaid_feature(self.company, self.feature, "2026-09", at=clock(2026, 10, 2))
        self.assertEqual(first, "charged")
        self.assertEqual(amount, Decimal("22.00"))
        self.assertEqual(second, "already_billed")
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("78.00"))
        self.assertEqual(CreditLedger.objects.filter(company=self.company, reference__startswith="feature-metered:").count(), 1)
        self.assertFalse(CreditLedger.objects.filter(company=self.other_company).exists())

    def test_pending_invoice_suspends_feature_without_negative_wallet(self):
        CreditWallet.objects.filter(company=self.company).update(balance=Decimal("0"))
        state, _ = activate_postpaid_feature(self.company, self.feature, at=clock(2026, 9, 1))
        meter_postpaid_feature(state, at=clock(2026, 10, 1))
        result, amount = settle_postpaid_feature(self.company, self.feature, "2026-09", at=clock(2026, 10, 2))
        self.assertEqual(result, "insufficient_credits")
        self.assertEqual(amount, Decimal("22.00"))
        state.refresh_from_db()
        self.assertTrue(state.suspended)
        self.assertFalse(user_has_permission(self.user, "jobs.view"))
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("0"))
        self.assertFalse(CreditLedger.objects.filter(reference__startswith="feature-metered:").exists())

    def test_storage_monthly_bill_real_media_bytes_and_no_duplicate(self):
        StorageCreditPolicy.objects.create(
            billing_period="monthly", included_gb=Decimal("1"),
            credits_per_gb=Decimal("10"), is_active=True,
        )
        MediaFile.objects.create(company=self.company, file="saas/monthly.bin", name="Real Media", size_bytes=2 * (1024 ** 3))
        first, _ = meter_company_storage(self.company, at=clock(2026, 9, 1))
        self.assertEqual(first, "metered")
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("100"))
        again, _ = meter_company_storage(self.company, at=clock(2026, 9, 1))
        self.assertEqual(again, "already_metered")
        meter_company_storage(self.company, at=clock(2026, 10, 1))
        period = StorageMeterPeriod.objects.get(company=self.company, period="2026-09")
        self.assertEqual(period.accrued_credits.quantize(Decimal("0.01")), Decimal("10.00"))
        self.assertEqual(StorageMeterReading.objects.filter(company=self.company).count(), 2)
        result, credits = settle_company_storage(self.company, "2026-09", at=clock(2026, 10, 2))
        self.assertEqual(result, "charged")
        self.assertEqual(credits, Decimal("10.00"))
        repeated, _ = settle_company_storage(self.company, "2026-09", at=clock(2026, 10, 2))
        self.assertEqual(repeated, "already_billed")
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("90.00"))
        self.assertEqual(CreditLedger.objects.filter(company=self.company, reference__startswith="storage-metered:").count(), 1)

    def test_legacy_storage_command_refuses_double_billing(self):
        StorageCreditPolicy.objects.create(billing_period="monthly", credits_per_gb=Decimal("5"), is_active=True)
        with self.assertRaisesRegex(CommandError, "Do NOT run legacy"):
            call_command("bill_storage_credits", "--dry-run", stdout=StringIO())

    def test_paid_feature_api_reports_postpaid_billing_mode(self):
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.user)
        listing = self.api_client.get("/api/v1/saas/credits/features")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.data[0]["billingMode"], "monthly_postpaid")
        enabled = self.api_client.post("/api/v1/saas/credits/features/enable", {"key": "jobs"}, format="json")
        self.assertEqual(enabled.status_code, 200)
        self.assertEqual(enabled.data["chargedCredits"], "0")
        self.assertEqual(enabled.data["billingMode"], "monthly_postpaid")
        self.assertTrue(user_has_permission(self.member, "jobs.view"))
        disabled = self.api_client.post("/api/v1/saas/credits/features/disable", {"key": "jobs"}, format="json")
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(user_has_permission(self.member, "jobs.view"))
