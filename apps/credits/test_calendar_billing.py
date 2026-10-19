"""Calendar-month closure, next-month due day and zero-credit grace regressions."""
from datetime import datetime
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, PlanFeature
from .calendar_billing import (
    deadline_for, finalize_month, overdue_statements,
    run_calendar_cycles, settle_statement,
)
from .models import (
    BillingCyclePolicy, BillingCycleStatement, CreditLedger, CreditWallet,
    FeatureMeterPeriod, StorageMeterCursor, StorageMeterPeriod,
    StorageCreditPolicy,
)


def dt(y, m, d, h=0, minute=0):
    return timezone.make_aware(datetime(y, m, d, h, minute), timezone.get_default_timezone())


@override_settings(
    STORAGE_MONTHLY_METERED_ENABLED=True,
    CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=True,
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
    CREDIT_POSTPAID_FEATURE_BILLING_ENABLED=False,
)
class CalendarBillingTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Calendar Workshop", slug="calendar-test", require_paid_features=False)
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER",
            permissions=["dashboard.view", "staff.view", "storage.view", "billing.view", "jobs.view"],
        )
        self.owner = User.objects.create_user(
            email="calendar-owner@example.test", name="Owner",
            password="Password2026Strong!", role=self.role, company=self.company,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.other = Company.objects.create(name="Unrelated", slug="unrelated-calendar-test")
        self.wallet = CreditWallet.objects.create(company=self.company, balance=Decimal("0.00"))
        self.policy = BillingCyclePolicy.objects.create(
            enabled=True, due_day=5, restrict_overdue=True,
            allow_zero_balance_usage=True, effective_month="2026-10",
        )
        StorageCreditPolicy.objects.create(
            included_gb=Decimal("0"), credits_per_gb=Decimal("12"),
            billing_mode="snapshot", billing_period="monthly", is_active=True,
        )
        StorageMeterCursor.objects.create(company=self.company, sampled_at=dt(2026, 11, 1, 0, 5))
        group = FeatureGroup.objects.create(key="calendar-test-ops", label="Operations")
        self.feature = PlanFeature.objects.create(key="jobs", label="Jobs", group=group)
        self.free_feature = PlanFeature.objects.create(key="staff", label="Staff", group=group)
        self.storage = StorageMeterPeriod.objects.create(
            company=self.company, period="2026-10", accrued_credits=Decimal("12.345"),
            covered_seconds=86400,
        )
        self.feature_period = FeatureMeterPeriod.objects.create(
            company=self.company, feature=self.feature, period="2026-10",
            accrued_credits=Decimal("17.50"), activation_credits=Decimal("0"),
            covered_seconds=86400,
        )
        FeatureMeterPeriod.objects.create(
            company=self.company, feature=self.free_feature, period="2026-10",
            accrued_credits=Decimal("55.00"), covered_seconds=3600,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_finalized_first_of_month_without_writing_ledger(self):
        state, invoice = finalize_month(self.company, "2026-10", at=dt(2026, 11, 1, 1))
        self.assertEqual(state, "finalized")
        self.assertEqual(invoice.total_credits, Decimal("29.85"))
        self.assertEqual(invoice.feature_credits, Decimal("17.50"))
        self.assertEqual(invoice.storage_credits, Decimal("12.35"))
        self.assertEqual(invoice.due_at, dt(2026, 11, 5, 0, 5))
        self.assertEqual(settle_statement(invoice, at=dt(2026, 11, 3))[0], "not_due")
        self.assertFalse(CreditLedger.objects.exists())
        self.assertEqual(finalize_month(self.company, "2026-10", at=dt(2026, 11, 3))[0], "already_finalized")

    def test_due_day_insufficient_does_not_create_partial_debit(self):
        _, bill = finalize_month(self.company, "2026-10", at=dt(2026, 11, 1, 1))
        self.wallet.balance = Decimal("20.00")
        self.wallet.save(update_fields=["balance"])
        self.assertEqual(settle_statement(bill, at=dt(2026, 11, 5, 0, 10))[0], "insufficient")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("20.00"))
        self.assertFalse(CreditLedger.objects.exists())
        self.assertEqual(len(overdue_statements(self.company, at=dt(2026, 11, 5, 23, 55))), 0)
        self.assertEqual(len(overdue_statements(self.company, at=dt(2026, 11, 6))), 1)

    def test_topup_then_retry_debits_once_and_restores_all_access(self):
        _, bill = finalize_month(self.company, "2026-10", at=dt(2026, 11, 1, 1))
        self.wallet.balance = Decimal("100.00")
        self.wallet.save(update_fields=["balance"])
        self.assertEqual(settle_statement(bill, at=dt(2026, 11, 5, 1))[0], "paid")
        self.assertEqual(settle_statement(bill, at=dt(2026, 11, 6))[0], "already_paid")
        self.assertEqual(CreditLedger.objects.filter(kind="usage_charge").count(), 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("70.15"))
        self.storage.refresh_from_db()
        self.feature_period.refresh_from_db()
        self.assertIsNotNone(self.storage.settled_at)
        self.assertIsNotNone(self.feature_period.settled_at)
        self.assertFalse(overdue_statements(self.company, at=dt(2026, 11, 6)))

    def test_zero_balance_does_not_block_usage_before_due_when_allowed(self):
        self.assertTrue(user_has_permission(self.owner, "dashboard.view"))
        self.assertTrue(user_has_permission(self.owner, "staff.view"))
        self.assertTrue(user_has_permission(self.owner, "billing.view"))
        self.assertEqual(self.wallet.balance, Decimal("0.00"))

    def test_cannot_modify_closed_month_after_finalization(self):
        finalize_month(self.company, "2026-10", at=dt(2026, 11, 1, 1))
        self.assertTrue(BillingCycleStatement.objects.filter(company=self.company, month="2026-10").exists())
        self.assertEqual(self.storage.accrued_credits, Decimal("12.345"))

    def test_owner_api_is_tenant_scoped_and_read_only(self):
        result = self.client.get("/api/v1/saas/credits/billing-cycle")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.data["enabled"])
        self.assertEqual(result.data["dueDay"], 5)
        self.assertFalse(result.data["restricted"])
        self.assertNotIn(str(self.other.pk), str(result.data))
        second_role = Role.objects.create(company=self.other, name="Viewer", code="EMP", permissions=["billing.view"])
        outsider = User.objects.create_user(email="outsider-cycle@example.test", name="Other", password="Example123456!", role=second_role, company=self.other)
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get("/api/v1/saas/credits/billing-cycle").status_code, 403)

    def test_legacy_engines_do_not_double_debit(self):
        from .monthly_storage import settle_company_storage
        from .monthly_features import settle_postpaid_feature
        self.assertEqual(settle_company_storage(self.company, "2026-10", at=dt(2026, 11, 5))[0], "calendar_policy")
        self.assertEqual(settle_postpaid_feature(self.company, self.feature, "2026-10", at=dt(2026, 11, 5))[0], "calendar_policy")
        self.assertFalse(CreditLedger.objects.exists())

    def test_month_end_closed_period_is_required(self):
        self.assertEqual(finalize_month(self.company, "2026-10", at=dt(2026, 10, 31, 20))[0], "awaiting_meter")
        StorageMeterCursor.objects.filter(company=self.company).update(sampled_at=dt(2026, 10, 31, 23, 30))
        self.assertEqual(finalize_month(self.company, "2026-10", at=dt(2026, 11, 1, 1))[0], "awaiting_meter")

    def test_due_policy_can_be_changed_only_for_new_statements(self):
        _, invoice = finalize_month(self.company, "2026-10", at=dt(2026, 11, 1, 1))
        self.policy.due_day = 10
        self.policy.save()
        self.assertEqual(BillingCycleStatement.objects.get(pk=invoice.pk).due_at, dt(2026, 11, 5, 0, 5))
        self.assertEqual(deadline_for("2026-11", 10), dt(2026, 12, 10, 0, 5))

    def test_no_retroactive_charges_before_activation_month(self):
        self.policy.effective_month = "2026-11"
        self.policy.save()
        self.assertEqual(finalize_month(self.company, "2026-10", at=dt(2026, 11, 1, 1))[0], "before_activation")
        self.assertEqual(run_calendar_cycles(at=dt(2026, 11, 5, 1)), {})
        self.assertFalse(CreditLedger.objects.exists())

    def test_overdue_switch_preserves_recovery(self):
        self.policy.effective_month = "2026-01"
        self.policy.save()
        BillingCycleStatement.objects.create(
            company=self.company, month="2026-05", due_at=dt(2026, 6, 5, 0, 5),
            feature_credits=Decimal("40"), total_credits=Decimal("40"),
        )
        self.assertFalse(user_has_permission(self.owner, "dashboard.view"))
        self.assertFalse(user_has_permission(self.owner, "staff.view"))
        self.assertTrue(user_has_permission(self.owner, "billing.view"))
        self.assertEqual(self.client.get("/api/v1/dashboard").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/saas/credits/billing-cycle").status_code, 200)
        self.policy.restrict_overdue = False
        self.policy.save(update_fields=["restrict_overdue"])
        self.assertTrue(user_has_permission(self.owner, "dashboard.view"))
