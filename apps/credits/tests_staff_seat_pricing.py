from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.credits.calendar_billing import finalize_month, settle_statement
from apps.credits.cycle_billing import activate_cycle_label, cycle_bounds, settle_cycle_category
from apps.credits.models import (
    BillingCyclePolicy, CreditCyclePolicy, CreditLedger, CreditWallet, CycleLabelSession,
    FeatureBundle, FeatureCreditPrice, FeatureMeterPeriod, ModulePriceLabel,
)
from apps.credits.monthly_features import activate_postpaid_feature, deactivate_postpaid_feature
from apps.employees.models import Employee
from apps.roles.models import Role
from apps.saas.models import PlanFeature


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
    STORAGE_MONTHLY_METERED_ENABLED=False,
)
class StaffSeatPricingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="Seat Pricing Workshop", slug="seat-pricing-workshop")
        owner_role = Role.objects.create(
            company=company, name="Super Admin", code="SUPER_ADMIN", permissions=["*"], is_system=True,
        )
        cls.company = company
        cls.owner = User.objects.create_user(email="owner@seat-pricing.test", company=company, role=owner_role)
        company.owner = cls.owner
        company.save(update_fields=["owner"])
        cls.role = Role.objects.create(
            company=company, name="Mechanic", code="MECHANIC", permissions=["jobs.view"],
        )
        cls.feature = PlanFeature.objects.get(key="accounts_tax")
        cls.bundle = FeatureBundle.objects.create(
            key="seat-pricing", name="Seat Pricing", price_credits=0, per_user_credits=Decimal("30"),
            billing_type="monthly",
        )
        cls.bundle.features.add(cls.feature)
        cls.wallet, _ = CreditWallet.objects.update_or_create(company=company, defaults={"balance": 100})

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def add_staff(self, at, phone, *, login=True):
        with patch("django.utils.timezone.now", return_value=at):
            response = self.api.post("/api/v1/employees", {
                "name": "Mechanic", "phone": phone, "roleId": str(self.role.pk), "role": "Mechanic",
                "employmentStatus": "Active", "loginAccessEnabled": login,
            }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def set_login(self, staff_id, at, enabled):
        with patch("django.utils.timezone.now", return_value=at):
            response = self.api.patch(
                f"/api/v1/employees/{staff_id}/login-access", {"enabled": enabled}, format="json",
            )
        self.assertEqual(response.status_code, 200, response.data)

    def assert_marketplace_quote(self, users, rate):
        response = self.api.get(reverse("credit-bundles"))
        self.assertEqual(response.status_code, 200, response.data)
        price = next(row for row in response.data if row["id"] == self.bundle.pk)
        self.assertEqual(price["userCount"], users)
        self.assertEqual(Decimal(price["totalCredits"]), Decimal(rate))

    def assert_wallet(self, balance, charges):
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal(balance))
        self.assertEqual(CreditLedger.objects.filter(company=self.company, delta__lt=0).count(), charges)

    @override_settings(CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=False)
    def test_fixed_cycle_uses_each_seat_rate_from_the_staff_change_time(self):
        start, end = cycle_bounds(timezone.make_aware(datetime(2026, 4, 5)))
        CreditCyclePolicy.objects.create(enabled=True, activated_at=start)
        with patch("django.utils.timezone.now", return_value=start):
            activate_cycle_label(self.company, self.bundle, self.owner)
        self.add_staff(start + timedelta(days=5), "+919333333333", login=False)
        self.assert_marketplace_quote(1, 30)
        self.assertEqual(CycleLabelSession.objects.filter(company=self.company).count(), 1)
        added = start + timedelta(days=10)
        staff_id = self.add_staff(added, "+919444444444")
        self.assert_marketplace_quote(2, 60)
        disabled = start + timedelta(days=20)
        self.set_login(staff_id, disabled, False)
        self.assert_marketplace_quote(1, 30)
        enabled = start + timedelta(days=24)
        self.set_login(staff_id, enabled, True)
        removed = start + timedelta(days=26)
        user_id = Employee.objects.get(pk=staff_id).user_id
        with patch("django.utils.timezone.now", return_value=removed):
            response = self.api.delete(f"/api/v1/employees/{staff_id}")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(User.objects.get(pk=user_id).is_active)
        self.assert_marketplace_quote(1, 30)
        segments = list(CycleLabelSession.objects.filter(company=self.company).order_by("started_at").values_list(
            "started_at", "ended_at", "user_seats", "monthly_credits",
        ))
        self.assertEqual(segments, [
            (start, added, 1, Decimal("30")),
            (added, disabled, 2, Decimal("60")),
            (disabled, enabled, 1, Decimal("30")),
            (enabled, removed, 2, Decimal("60")),
            (removed, None, 1, Decimal("30")),
        ])
        status, _ = settle_cycle_category(self.company, "feature", start, end, at=removed)
        self.assertEqual(status, "period_open")
        self.assert_wallet(100, 0)
        status, invoice = settle_cycle_category(self.company, "feature", start, end, at=end)
        self.assertEqual(status, "paid")
        self.assertEqual(invoice.credits_due, Decimal("42"))
        status, _ = settle_cycle_category(self.company, "feature", start, end, at=end)
        self.assertEqual(status, "already_paid")
        self.assert_wallet(58, 1)

    @override_settings(CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=True)
    def test_calendar_billing_snapshots_rates_even_with_storage_event_tracking_off(self):
        BillingCyclePolicy.objects.create(enabled=True, calculation_interval_seconds=3600)
        self.bundle.billing_type = "hourly"
        self.bundle.per_user_credits = 10
        self.bundle.save(update_fields=["billing_type", "per_user_credits"])
        ModulePriceLabel.objects.update_or_create(feature=self.feature, defaults={
            "bundle": self.bundle, "monthly_postpaid": True,
        })
        FeatureCreditPrice.objects.update_or_create(feature=self.feature, defaults={
            "per_user_credits": 10, "base_credits": 0, "per_staff_credits": 0,
            "activation_credits": 0, "billing_period": "hourly", "is_available": True,
        })
        start = timezone.make_aware(datetime(2026, 4, 30, 20))
        with patch("django.utils.timezone.now", return_value=start):
            state, _ = activate_postpaid_feature(self.company, self.feature)
        added = start + timedelta(hours=1)
        staff_id = self.add_staff(added, "+919444444444")
        self.assert_marketplace_quote(2, 20)
        state.refresh_from_db()
        self.assertEqual((state.sampled_at, state.monthly_rate), (added, Decimal("20")))
        disabled = start + timedelta(hours=2)
        self.set_login(staff_id, disabled, False)
        self.assert_marketplace_quote(1, 10)
        state.refresh_from_db()
        self.assertEqual((state.sampled_at, state.monthly_rate), (disabled, Decimal("10")))
        ended = start + timedelta(hours=3)
        with patch("django.utils.timezone.now", return_value=ended):
            deactivate_postpaid_feature(self.company, self.feature)
        period = FeatureMeterPeriod.objects.get(company=self.company, feature=self.feature, period="2026-04")
        self.assertEqual((period.covered_seconds, period.accrued_credits), (10800, Decimal("40")))
        closed = timezone.make_aware(datetime(2026, 5, 1))
        status, statement = finalize_month(self.company, "2026-04", at=closed)
        self.assertEqual(status, "finalized")
        status, _ = settle_statement(statement, at=closed)
        self.assertEqual(status, "not_due")
        self.assert_wallet(100, 0)
        status, _ = settle_statement(statement, at=statement.due_at)
        self.assertEqual(status, "paid")
        status, _ = settle_statement(statement, at=statement.due_at)
        self.assertEqual(status, "already_paid")
        self.assert_wallet(60, 1)
