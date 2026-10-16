"""Monthly 5th-to-5th billing is opt-in, observed, retryable and role-secure."""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, MediaFile, PlanFeature, StorageEvent
from apps.credits.models import (
    CreditCycleInvoice, CreditCyclePolicy, CreditLedger, CreditWallet,
    FeatureBundle, CycleLabelSession, StorageCreditPolicy,
)
from apps.credits.cycle_billing import (
    activate_cycle_label, company_is_overdue, cycle_bounds, cycle_status,
    feature_cycle_quote, settle_cycle_category, storage_cycle_quote,
)


def dt(month, day):
    return datetime(2026, month, day, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=True,
)
class ConfigurableCycleTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Cycle Garage", slug="cycle-garage",
            require_paid_features=True,
        )
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER",
            permissions=["billing.view", "billing.edit", "dashboard.view",
                         "storage.view", "storage.manage", "jobs.view", "staff.view"],
        )
        self.owner = User.objects.create_user(
            email="cycle-owner@example.test", name="Cycle Owner",
            password="StrongPassword2026!", company=self.company, role=self.role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.wallet = CreditWallet.objects.create(company=self.company, balance=Decimal("0"))
        self.policy = CreditCyclePolicy.objects.create(
            day_of_month=5, enabled=True, block_overdue=True,
            activated_at=dt(10, 6),
        )
        self.group = FeatureGroup.objects.create(key="cycle-tests", label="Ops")
        self.feature = PlanFeature.objects.create(key="jobs", label="Jobs", group=self.group)
        self.bundle = FeatureBundle.objects.create(
            key="cycle-jobs", name="Jobs", billing_type="monthly",
            per_user_credits=Decimal("30.00"), price_credits=Decimal("0"),
        )
        self.bundle.features.add(self.feature)

    def test_cycle_bounds_are_exact_midnight_with_year_rollover(self):
        self.assertEqual(cycle_bounds(dt(10, 10), 5), (dt(10, 5), dt(11, 5)))
        self.assertEqual(cycle_bounds(dt(10, 3), 5), (dt(9, 5), dt(10, 5)))
        self.assertEqual(cycle_bounds(datetime(2027, 1, 1, tzinfo=ZoneInfo("Asia/Kolkata")), 5)[0].date().isoformat(), "2026-12-05")
        with self.assertRaises(ValueError):
            cycle_bounds(dt(10, 15), 30)

    def test_zero_credits_allow_new_monthly_label_activation_with_role_permission(self):
        access, charged = activate_cycle_label(self.company, self.bundle, self.owner)
        self.assertEqual(charged, Decimal("0"))
        self.assertEqual(self.wallet.balance, Decimal("0"))
        self.assertTrue(access.enabled)
        self.assertTrue(CycleLabelSession.objects.filter(company=self.company, bundle=self.bundle, ended_at__isnull=True).exists())
        self.assertEqual(CreditLedger.objects.count(), 0)
        self.assertTrue(user_has_permission(self.owner, "jobs.view"))
        self.assertEqual(self.client.get("/api/v1/jobs").status_code, 200)
        r = self.client.get("/api/v1/saas/credits/bundles")
        offer = next(b for b in r.data if b["id"] == self.bundle.pk)
        self.assertTrue(offer["cyclePostpaid"])

    def test_feature_usage_is_proportional_and_does_not_charge_free_modules(self):
        CycleLabelSession.objects.create(
            company=self.company, bundle=self.bundle, started_at=dt(10, 10),
            ended_at=dt(10, 20), monthly_credits=Decimal("31.00"), user_seats=1,
        )
        charge, details = feature_cycle_quote(self.company, dt(10, 5), dt(11, 5), self.policy)
        self.assertEqual(charge, Decimal("10.00"))
        self.assertEqual(details["labels"][0]["seconds"], str(10 * 86400) + ".0")

    def test_storage_upload_and_delete_are_billed_only_for_observed_time(self):
        StorageCreditPolicy.objects.create(
            billing_period="monthly", billing_mode="snapshot", is_active=True,
            included_gb=Decimal("0"), credits_per_gb=Decimal("31"),
        )
        events = [
            (dt(10, 10), "UPLOAD", 1024 ** 3),
            (dt(10, 20), "DELETE", 0),
        ]
        for point, kind, after in events:
            StorageEvent.objects.create(
                company=self.company, event_type=kind,
                file_id_snapshot="00aa0000-0000-4000-8000-000000000001",
                name_snapshot="already-deleted-photo.jpg",
                file_size_bytes=1024 ** 3,
                delta_bytes=(1 if kind == "UPLOAD" else -1) * 1024 ** 3,
                storage_after_bytes=after, occurred_at=point,
            )
        amount, detail = storage_cycle_quote(self.company, dt(10, 5), dt(11, 5), self.policy)
        self.assertEqual(amount, Decimal("10.00"))
        self.assertEqual([e["eventType"] for e in detail["fileEvents"]], ["UPLOAD", "DELETE"])
        self.assertEqual(detail["fileEvents"][1]["filename"], "already-deleted-photo.jpg")

    def test_insufficient_wallet_persists_debt_and_settlement_retries_idempotently(self):
        CycleLabelSession.objects.create(
            company=self.company, bundle=self.bundle,
            started_at=dt(10, 6), monthly_credits=Decimal("31.00"),
        )
        end = dt(11, 5)
        state, row = settle_cycle_category(
            self.company, "feature", dt(10, 5), end, at=end,
        )
        self.assertEqual(state, "insufficient")
        self.assertEqual(row.credits_due, Decimal("30.00"))
        self.assertEqual(CreditLedger.objects.count(), 0)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("0"))
        # Billing lock uses *real* current time; create an earlier due invoice
        # to prove the hold without mocking the actual clock.
        CreditCycleInvoice.objects.create(
            company=self.company, category="storage",
            starts_on=(timezone.localdate() - timedelta(days=70)),
            ends_at=timezone.now() - timedelta(days=32),
            due_at=timezone.now() - timedelta(days=32),
            credits_due=Decimal("5.00"), status="insufficient",
        )
        self.assertTrue(company_is_overdue(self.company))
        self.assertFalse(user_has_permission(self.owner, "dashboard.view"))
        self.assertTrue(user_has_permission(self.owner, "billing.view"))
        self.assertTrue(user_has_permission(self.owner, "storage.view"))
        self.wallet.balance = Decimal("80")
        self.wallet.save(update_fields=["balance"])
        status, paid = settle_cycle_category(
            self.company, "feature", dt(10, 5), end, at=end + timedelta(hours=1),
        )
        self.assertEqual(status, "paid")
        self.assertEqual(paid.status, "paid")
        again, _ = settle_cycle_category(
            self.company, "feature", dt(10, 5), end, at=end + timedelta(hours=2),
        )
        self.assertEqual(again, "already_paid")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("50"))
        self.assertEqual(CreditLedger.objects.filter(reference__startswith="credit-cycle:").count(), 1)

    def test_api_billing_history_is_owner_only_and_tenant_scoped(self):
        r = self.client.get("/api/v1/saas/credits/cycle")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["dayOfMonth"], 5)
        self.assertFalse(r.data["overdue"])
        self.assertEqual(r.data["invoices"], [])
        outsider = Company.objects.create(name="Other", slug="cycle-other")
        role = Role.objects.create(company=outsider, name="Staff", code="STAFF", permissions=["staff.view"])
        staff = User.objects.create_user(
            email="cycle-other@example.test", name="Other", company=outsider, role=role,
        )
        self.client.force_authenticate(staff)
        self.assertEqual(self.client.get("/api/v1/saas/credits/cycle").status_code, 403)

    def test_manual_legacy_snapshot_command_refuses_cycle_double_charge(self):
        with self.assertRaises(CommandError):
            call_command("bill_storage_credits", "--dry-run")
        with self.assertRaises(CommandError):
            call_command("charge_credit_features", "--dry-run")

    def test_disabled_policy_never_creates_invoices_or_enforces_hold(self):
        self.policy.enabled = False
        self.policy.save(update_fields=["enabled"])
        self.assertFalse(company_is_overdue(self.company))
        self.assertFalse(cycle_status(self.company)["enabled"])
