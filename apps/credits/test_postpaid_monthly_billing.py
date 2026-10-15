"""Contract tests: postpaid feature seconds, storage events, month-end wallet debit."""
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, PlanFeature, StorageEvent
from apps.credits.models import (
    CompanyCreditFeature, CreditLedger, CreditWallet,
    FeatureCreditPrice, MonthlyCreditStatement, PostpaidFeatureState,
    PostpaidUsageRecord, StorageCreditPolicy,
)
from apps.credits.postpaid_billing import (
    enable_postpaid_feature, disable_postpaid_feature, meter_feature_state,
    settle_company_month, storage_month_credits,
)
from apps.credits.tasks import (
    bill_due_storage_credits, renew_due_credit_features,
    record_postpaid_usage, settle_monthly_postpaid_credits,
)

TZ = ZoneInfo("Asia/Kolkata")
T0 = datetime(2026, 10, 1, 0, 0, 0, tzinfo=TZ)
TM = datetime(2026, 10, 16, 0, 0, 0, tzinfo=TZ)
T1 = datetime(2026, 11, 1, 0, 0, 0, tzinfo=TZ)


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
    CREDIT_POSTPAID_BILLING_ENABLED=True,
    CREDIT_AUTOMATIC_BILLING_ENABLED=True,
    TIME_ZONE="Asia/Kolkata",
)
class PostpaidCreditsContractTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Monthly Workshop", slug="monthly-workshop")
        self.other = Company.objects.create(name="Other Workshop", slug="monthly-other")
        role = Role.objects.create(
            company=self.company, name="Billing Manager", code="BILLING_MGR",
            permissions=["jobs.view", "billing.view", "billing.edit"],
        )
        self.owner = User.objects.create_user(
            email="monthly-owner@example.test", name="Owner",
            password="SafeMonthPass123!", company=self.company, role=role,
        )
        self.member = User.objects.create_user(
            email="monthly-member@example.test", name="Member",
            password="SafeMonthPass123!", company=self.company, role=role,
        )
        self.group = FeatureGroup.objects.create(key="monthly-test", label="Monthly")
        self.feature = PlanFeature.objects.create(
            key="jobs", label="Job Cards", group=self.group,
        )
        self.rate = FeatureCreditPrice.objects.create(
            feature=self.feature, base_credits=Decimal("10.00"),
            per_user_credits=Decimal("5.00"),
            activation_credits=Decimal("2.00"),
            billing_period="monthly", is_available=True,
        )
        self.wallet = CreditWallet.objects.create(
            company=self.company, balance=Decimal("100.00"),
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_full_month_two_users_plus_activation_charged_once_after_month_end(self):
        state, charged = enable_postpaid_feature(
            self.company, self.feature, self.rate, self.owner, now=T0,
        )
        self.assertEqual(charged, Decimal("0.00"))
        self.assertTrue(state.enabled)
        self.assertIsNone(state.next_charge_at)
        self.assertTrue(user_has_permission(self.owner, "jobs.view"))
        self.assertTrue(user_has_permission(self.member, "jobs.view"))
        _, second = enable_postpaid_feature(
            self.company, self.feature, self.rate, self.owner, now=T0 + timedelta(seconds=5),
        )
        self.assertEqual(second, Decimal("0.00"))
        self.assertEqual(PostpaidUsageRecord.objects.filter(kind="feature_activation").count(), 1)
        meter = PostpaidFeatureState.objects.get(company=self.company, feature=self.feature)
        self.assertEqual(meter_feature_state(meter, now=T1), 1)
        self.assertEqual(meter_feature_state(meter, now=T1), 0)
        self.assertEqual(
            PostpaidUsageRecord.objects.filter(kind="feature_usage").count(), 1,
        )
        self.assertEqual(
            PostpaidUsageRecord.objects.get(kind="feature_usage").credits,
            Decimal("20.00000000"),
        )
        self.assertEqual(CreditLedger.objects.count(), 0)
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("100.00"))
        status, invoice = settle_company_month(
            self.company, "2026-10", now=T1 + timedelta(minutes=1),
        )
        self.assertEqual(status, "charged")
        self.assertEqual(invoice.feature_credits, Decimal("22.00"))
        self.assertEqual(invoice.storage_credits, Decimal("0.00"))
        self.assertEqual(invoice.total_credits, Decimal("22.00"))
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("78.00"))
        repeated, _ = settle_company_month(self.company, "2026-10", now=T1 + timedelta(hours=1))
        self.assertEqual(repeated, "already_paid")
        self.assertEqual(CreditLedger.objects.filter(kind="monthly_usage").count(), 1)

    def test_disable_closes_usage_at_exact_time_and_blocks_feature_for_all_users(self):
        enable_postpaid_feature(self.company, self.feature, self.rate, self.owner, now=T0)
        self.assertTrue(disable_postpaid_feature(
            self.company, self.feature, self.owner, now=TM,
        ))
        self.assertFalse(user_has_permission(self.owner, "jobs.view"))
        self.assertFalse(user_has_permission(self.member, "jobs.view"))
        meter = PostpaidUsageRecord.objects.get(kind="feature_usage")
        self.assertEqual(meter.until_at, TM)
        self.assertEqual(meter.user_count, 2)
        self.assertGreater(meter.credits, Decimal("0"))
        self.assertLess(meter.credits, Decimal("20"))
        self.assertEqual(CreditLedger.objects.count(), 0)

    def test_storage_partial_month_events_bill_actual_gib_seconds(self):
        policy = StorageCreditPolicy.objects.create(
            billing_period="monthly", included_gb=Decimal("1.000"),
            credits_per_gb=Decimal("4.00"), is_active=True,
        )
        half = datetime(2026, 10, 16, 12, 0, 0, tzinfo=TZ)
        StorageEvent.objects.create(
            company=self.company, event_type="UPLOAD",
            file_id_snapshot="00000000-0000-4000-8000-000000000001",
            name_snapshot="lecture.mp4", file_size_bytes=2 * 1024 ** 3,
            delta_bytes=2 * 1024 ** 3, storage_before_bytes=0,
            storage_after_bytes=2 * 1024 ** 3,
            occurred_at=T0,
        )
        StorageEvent.objects.create(
            company=self.company, event_type="DELETE",
            file_id_snapshot="00000000-0000-4000-8000-000000000001",
            name_snapshot="lecture.mp4", file_size_bytes=2 * 1024 ** 3,
            delta_bytes=-2 * 1024 ** 3, storage_before_bytes=2 * 1024 ** 3,
            storage_after_bytes=0,
            occurred_at=half,
        )
        result, meta = storage_month_credits(self.company, T0, T1, policy)
        self.assertEqual(result, Decimal("2.00"))
        self.assertEqual(meta["events"], 2)
        status, invoice = settle_company_month(self.company, "2026-10", now=T1)
        self.assertEqual(status, "charged")
        self.assertEqual(invoice.storage_credits, Decimal("2.00"))
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("98.00"))

    def test_insufficient_credit_stays_pending_until_topup_then_only_one_debit(self):
        CreditWallet.objects.filter(company=self.company).update(balance=Decimal("1"))
        enable_postpaid_feature(self.company, self.feature, self.rate, self.owner, now=T0)
        meter_feature_state(
            PostpaidFeatureState.objects.get(company=self.company), now=T1,
        )
        result, invoice = settle_company_month(self.company, "2026-10", now=T1)
        self.assertEqual(result, "insufficient_credits")
        self.assertEqual(invoice.status, "pending")
        self.assertEqual(CreditLedger.objects.count(), 0)
        self.assertFalse(user_has_permission(self.owner, "jobs.view"))
        CreditWallet.objects.filter(company=self.company).update(balance=Decimal("30"))
        retry, invoice = settle_company_month(self.company, "2026-10", now=T1)
        self.assertEqual(retry, "charged")
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("8.00"))
        self.assertTrue(user_has_permission(self.owner, "jobs.view"))
        self.assertEqual(CreditLedger.objects.count(), 1)

    def test_entitlements_and_statements_are_tenant_scoped(self):
        enable_postpaid_feature(self.company, self.feature, self.rate, self.owner, now=T0)
        path = "/api/v1/saas/credits/entitlements"
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["enforced"])
        self.assertIn("jobs", response.data["modules"])
        self.assertTrue(response.data["canManageFeatures"])
        other_role = Role.objects.create(
            company=self.other, name="Other Manager", code="OTHER_BILL",
            permissions=["billing.view", "jobs.view"],
        )
        other_user = User.objects.create_user(
            email="another-billing@example.test", name="Other",
            password="SafeMonthPass123!", company=self.other, role=other_role,
        )
        self.client.force_authenticate(user=other_user)
        second = self.client.get(path)
        self.assertEqual(second.status_code, 200)
        self.assertNotIn("jobs", second.data["modules"])
        self.assertEqual(self.client.get("/api/v1/saas/credits/monthly-statements").data, [])

    def test_cannot_settle_month_before_end(self):
        with self.assertRaisesRegex(ValueError, "unfinished"):
            settle_company_month(self.company, "2026-10", now=TM)


class DisabledPostpaidBillingTests(TestCase):
    @override_settings(CREDIT_AUTOMATIC_BILLING_ENABLED=False, CREDIT_POSTPAID_BILLING_ENABLED=False)
    def test_no_scheduled_task_mutates_wallet_while_disabled(self):
        with patch("django.core.management.call_command") as command:
            self.assertIn("skipped", renew_due_credit_features())
            self.assertIn("skipped", bill_due_storage_credits())
            self.assertIn("skipped", record_postpaid_usage())
            self.assertIn("skipped", settle_monthly_postpaid_credits())
            command.assert_not_called()
