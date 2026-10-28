"""Regressions for company-scoped pricing audit and separate credit sources."""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.companies.models import Company
from apps.credits.company_feature_pricing_logs import company_feature_pricing_report
from apps.credits.models import (
    CompanyBundleAccess, CompanyPostpaidFeature, CreditLedger,
    FeatureBundle, FeatureMeterReading,
)
from apps.saas.models import FeatureGroup, PlanFeature, SecurityEvent


class CompanyFeaturePricingLogTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Alpha Workshop", slug="alpha-workshop")
        self.other = Company.objects.create(name="Beta Workshop", slug="beta-workshop")
        group, _ = FeatureGroup.objects.get_or_create(
            key="service-modules", defaults={"label": "Workshop Modules"},
        )
        # The migration catalog already publishes built-in modules such as
        # attendance. Reuse that catalog row so this fixture stays valid after
        # migrations seed the production feature registry.
        self.feature, _ = PlanFeature.objects.get_or_create(
            key="attendance",
            defaults={"label": "Attendance & Punches", "group": group},
        )
        self.bundle = FeatureBundle.objects.create(
            key="attendance-paid", name="Attendance & Punches",
            price_credits=Decimal("0"),
            per_user_credits=Decimal("7.00"),
            billing_type="hourly",
        )
        self.bundle.features.add(self.feature)

    def test_prepaid_entitlement_is_not_confused_with_current_price(self):
        CompanyBundleAccess.objects.create(
            company=self.company, bundle=self.bundle, enabled=True,
            billing_type_at_purchase="hourly",
            last_price_credits=Decimal("21.00"),
        )
        self.bundle.per_user_credits = Decimal("12.00")
        self.bundle.save(update_fields=["per_user_credits"])

        report = company_feature_pricing_report(self.company)
        access = report["entitlements"][0]
        self.assertEqual(access["recorded_price"], Decimal("21.00"))
        self.assertEqual(access["current_per_user_price"], Decimal("12.00"))
        self.assertEqual(access["billing_period"], "Hourly (1 hour)")
        self.assertEqual(report["logs"], [])

    def test_actual_readings_and_wallet_debits_are_distinct_and_tenant_scoped(self):
        FeatureMeterReading.objects.create(
            company=self.company, feature=self.feature,
            sampled_at=timezone.now(), users=3, staff=0,
            monthly_rate=Decimal("36.00"),
            covered_seconds=3600, accrued_credits=Decimal("0.05"),
        )
        CreditLedger.objects.create(
            company=self.company, kind="bundle_charge", delta=Decimal("-21.00"),
            balance_after=Decimal("70.00"),
            description="Feature label: Attendance (hourly); 3 login users",
            reference="bundle-purchase:alpha-workshop-test",
        )
        CreditLedger.objects.create(
            company=self.other, kind="bundle_charge", delta=Decimal("-99.00"),
            balance_after=Decimal("10.00"),
            description="Other tenant feature purchase",
            reference="bundle-purchase:beta-workshop-test",
        )
        SecurityEvent.objects.create(
            company=self.company, event="feature_label_activated",
            metadata={"bundleId": self.bundle.pk, "name": self.bundle.name,
                      "credits": "21.00", "userCount": 3, "billingType": "hourly"},
        )
        report = company_feature_pricing_report(self.company)
        self.assertEqual(len(report["logs"]), 3)
        self.assertEqual({r["source"] for r in report["logs"]},
                         {"Wallet ledger", "Meter", "Access event"})
        self.assertFalse(any("Other tenant" in r["name"] for r in report["logs"]))
        meter = next(row for row in report["logs"] if row["source"] == "Meter")
        ledger = next(row for row in report["logs"] if row["source"] == "Wallet ledger")
        self.assertEqual(meter["credits"], Decimal("0.05"))
        self.assertEqual(meter["credit_kind"], "Accrued")
        self.assertEqual(ledger["credits"], Decimal("-21.00"))
        self.assertEqual(ledger["credit_kind"], "Wallet change")
        self.assertEqual(meter["users"], 3)
        self.assertEqual(meter["rate"], Decimal("36.00"))

    def test_postpaid_access_cannot_masquerade_as_prepaid_purchase(self):
        CompanyPostpaidFeature.objects.create(
            company=self.company, feature=self.feature,
            enabled=True, monthly_rate=Decimal("50.00"),
        )
        report = company_feature_pricing_report(self.company)
        self.assertEqual(report["entitlements"][0]["billing_period"], "Monthly postpaid")
        self.assertIsNone(report["entitlements"][0]["recorded_price"])
        self.assertTrue(report["entitlements"][0]["enabled"])
        self.assertFalse(company_feature_pricing_report(self.other)["entitlements"])

    def test_report_limit_validation(self):
        with self.assertRaises(ValueError):
            company_feature_pricing_report(self.company, limit=0)
        with self.assertRaises(ValueError):
            company_feature_pricing_report(self.company, limit=101)

    def test_template_compiles(self):
        from django.template.loader import get_template
        template = get_template("control_panel/company_detail.html")
        self.assertIsNotNone(template)
