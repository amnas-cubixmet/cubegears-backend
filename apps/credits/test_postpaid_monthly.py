"""Billing modes, prorated monthly usage, idempotency and no double debits."""
from datetime import datetime, timedelta, timezone as utc
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, PlanFeature, MediaFile
from apps.credits.models import (
    CompanyCreditFeature, CreditLedger, CreditWallet, FeatureCreditPrice,
    FeatureUsageSession, MonthlyUsageInvoice, StorageCreditPolicy, StorageUsageInterval,
)
from apps.credits.service import enable_paid_feature, disable_paid_feature, renew_feature
from apps.credits.postpaid import (
    feature_month_quote, record_storage_interval, settle_monthly_usage, month_bounds,
)
from apps.credits.storage_billing import charge_storage_period


@override_settings(CREDIT_POSTPAID_FEATURE_BILLING_ENABLED=True,
                   CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
                   CREDIT_STRICT_MODULE_GATING_ENABLED=True)
class PostpaidFeatureTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Postpaid Garage", slug="postpaid-garage")
        self.role = Role.objects.create(
            company=self.company, name="Admin", code="ADMIN",
            permissions=["billing.view", "billing.edit", "jobs.view"],
        )
        self.user = User.objects.create_user(
            email="postpaid-feature@example.test", name="Paid User",
            password="StrongPostpaidPassword123!", company=self.company, role=self.role,
        )
        group = FeatureGroup.objects.create(key="postpaid", label="Postpaid")
        self.feature = PlanFeature.objects.create(key="jobs", label="Job Cards", group=group)
        self.price = FeatureCreditPrice.objects.create(
            feature=self.feature, base_credits=Decimal("30.00"),
            activation_credits=Decimal("5.00"), billing_period="monthly",
        )
        CreditWallet.objects.create(company=self.company, balance=Decimal("100.00"))

    def test_enabling_and_disabling_records_usage_without_prepaid_charge(self):
        state, charged = enable_paid_feature(self.company, self.feature, self.user)
        self.assertEqual(charged, Decimal("0.00"))
        self.assertTrue(state.enabled)
        self.assertIsNone(state.next_charge_at)
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("100.00"))
        session = FeatureUsageSession.objects.get(company=self.company)
        self.assertEqual(session.monthly_credits, Decimal("30.00"))
        self.assertEqual(session.activation_credits, Decimal("5.00"))
        self.assertIsNone(session.ended_at)
        again, cost = enable_paid_feature(self.company, self.feature, self.user)
        self.assertEqual(cost, Decimal("0.00"))
        self.assertEqual(FeatureUsageSession.objects.count(), 1)
        self.assertEqual(renew_feature(state), "postpaid")
        self.assertEqual(CreditLedger.objects.filter(kind="feature_charge").count(), 0)
        disable_paid_feature(self.company, self.feature, self.user)
        session.refresh_from_db()
        self.assertIsNotNone(session.ended_at)

    def test_partial_month_debit_and_retry_is_idempotent(self):
        start, end = month_bounds("2026-09")
        FeatureUsageSession.objects.create(
            company=self.company, feature=self.feature,
            started_at=start, ended_at=start + timedelta(days=15),
            monthly_credits=Decimal("30.00"),
            activation_credits=Decimal("5.00"), user_seats=1,
        )
        amount, entries = feature_month_quote(self.company, "2026-09")
        self.assertEqual(amount, Decimal("20.00"))
        self.assertEqual(len(entries), 1)
        when = end + timedelta(minutes=30)
        first, invoice = settle_monthly_usage(self.company, "feature", "2026-09", now=when)
        self.assertEqual(first, "paid")
        self.assertEqual(invoice.credits_due, Decimal("20.00"))
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("80.00"))
        second, _ = settle_monthly_usage(self.company, "feature", "2026-09", now=when)
        self.assertEqual(second, "already_paid")
        self.assertEqual(CreditLedger.objects.filter(company=self.company, kind="feature_charge").count(), 1)

    def test_unpaid_invoice_can_be_retried_without_negative_wallet(self):
        start, end = month_bounds("2026-09")
        FeatureUsageSession.objects.create(
            company=self.company, feature=self.feature, started_at=start, ended_at=end,
            monthly_credits=Decimal("130.00"),
        )
        state, invoice = settle_monthly_usage(self.company, "feature", "2026-09", now=end + timedelta(hours=1))
        self.assertEqual(state, "insufficient")
        self.assertEqual(invoice.status, "insufficient")
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("100.00"))
        self.assertFalse(CreditLedger.objects.filter(kind="feature_charge").exists())
        CreditWallet.objects.filter(company=self.company).update(balance=Decimal("200.00"))
        state, invoice = settle_monthly_usage(self.company, "feature", "2026-09", now=end + timedelta(hours=2))
        self.assertEqual(state, "paid")
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("70.00"))


class MeteredStorageTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Metered Storage", slug="metered-storage-example")
        CreditWallet.objects.create(company=self.company, balance=Decimal("100.00"))
        self.policy = StorageCreditPolicy.objects.create(
            billing_period="monthly", billing_mode="metered_monthly",
            meter_interval_seconds=3600, included_gb=Decimal("0"),
            credits_per_gb=Decimal("30.00"), is_active=True,
        )
        MediaFile.objects.create(
            company=self.company, file="saas/metered-lecture.mp4",
            name="Metered upload", size_bytes=1024 ** 3,
        )
        self.at = datetime(2026, 9, 20, 8, tzinfo=utc.utc)
        StorageCreditPolicy.objects.filter(pk=self.policy.pk).update(
            updated_at=self.at - timedelta(days=2)
        )
        self.policy.refresh_from_db()

    def test_metering_records_verified_size_without_wallet_debit(self):
        result, sample = record_storage_interval(self.company, now=self.at, policy=self.policy)
        self.assertEqual(result, "metered")
        self.assertEqual(sample.measured_bytes, 1024 ** 3)
        self.assertGreater(sample.credits_accrued, Decimal("0"))
        self.assertEqual(sample.billing_month, "2026-09")
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("100.00"))
        retry, _ = record_storage_interval(self.company, now=self.at, policy=self.policy)
        self.assertEqual(retry, "not_due")
        self.assertEqual(StorageUsageInterval.objects.count(), 1)

    def test_monthly_storage_invoice_charges_once_only(self):
        record_storage_interval(self.company, now=self.at, policy=self.policy)
        end = month_bounds("2026-09")[1]
        result, invoice = settle_monthly_usage(
            self.company, "storage", "2026-09", now=end + timedelta(minutes=21)
        )
        self.assertEqual(result, "paid")
        self.assertGreater(invoice.credits_due, Decimal("0.00"))
        self.assertLess(invoice.credits_due, Decimal("30.00"))
        result, _ = settle_monthly_usage(
            self.company, "storage", "2026-09", now=end + timedelta(minutes=25)
        )
        self.assertEqual(result, "already_paid")
        self.assertEqual(CreditLedger.objects.filter(
            company=self.company, kind="storage_charge"
        ).count(), 1)

    def test_existing_snapshot_period_never_double_bills(self):
        record_storage_interval(self.company, now=self.at, policy=self.policy)
        CreditLedger.objects.create(
            company=self.company, kind="storage_charge",
            delta=Decimal("-30.00"), balance_after=Decimal("70.00"),
            reference=f"storage:{self.company.pk}:monthly:2026-09",
            description="Previously billed legacy storage snapshot",
        )
        state, invoice = settle_monthly_usage(
            self.company, "storage", "2026-09",
            now=month_bounds("2026-09")[1] + timedelta(minutes=30),
        )
        self.assertEqual(state, "already_snapshot_billed")
        self.assertIsNone(invoice)
        self.assertEqual(CreditLedger.objects.filter(
            company=self.company, kind="storage_charge"
        ).count(), 1)

    def test_old_snapshot_command_cannot_charge_metered_policy(self):
        result, _ = charge_storage_period(self.company, self.policy)
        self.assertEqual(result, "disabled")
        self.assertFalse(CreditLedger.objects.exists())

    def test_no_samples_does_not_create_fake_invoice(self):
        end = month_bounds("2026-09")[1]
        result, invoice = settle_monthly_usage(
            self.company, "storage", "2026-09", now=end + timedelta(minutes=30)
        )
        self.assertEqual(result, "no_samples")
        self.assertIsNone(invoice)
        self.assertFalse(MonthlyUsageInvoice.objects.exists())

    def test_meter_requires_safe_interval_boundaries(self):
        self.policy.meter_interval_seconds = 1
        self.policy.save()
        with self.assertRaisesRegex(ValueError, "60"):
            record_storage_interval(self.company, now=self.at, policy=self.policy)
