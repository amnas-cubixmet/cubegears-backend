"""Regression tests for purchased/metered module billing invariants.

The pricing page may let platform staff edit credits or publishing, but
never rewrite periods/models that companies have already purchased or metered.
"""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.credits.module_pricing import ModulePriceForm


class ModulePriceBillingValidationTests(SimpleTestCase):
    MODULES = [{"key": "attendance", "label": "Attendance", "billable": True}]

    def build_and_validate(self, *, period="monthly", postpaid=False,
                           existing=False, purchased=False, metered=False):
        bundle = SimpleNamespace(
            billing_type="monthly",
            company_access=SimpleNamespace(exists=lambda: purchased),
        )
        mapping = SimpleNamespace(
            bundle=bundle,
            feature=object(),
            monthly_postpaid=True,
        ) if existing else None
        data = {
            "module": "attendance",
            "per_user_credits": "12.50",
            "billing_type": period,
            "published": "on",
        }
        if postpaid:
            data["monthly_postpaid"] = "on"

        with (
            patch("apps.credits.module_pricing.module_inventory", return_value=self.MODULES),
            patch("apps.credits.module_pricing.ModulePriceLabel.objects.select_related") as labels,
            patch("apps.credits.module_pricing.CompanyPostpaidFeature.objects.filter") as meters,
        ):
            labels.return_value.filter.return_value.first.return_value = mapping
            meters.return_value.exists.return_value = metered
            form = ModulePriceForm(data)
            return form.is_valid(), form.errors

    def test_new_monthly_postpaid_pricing_is_valid(self):
        valid, errors = self.build_and_validate(postpaid=True)
        self.assertTrue(valid, errors)

    def test_postpaid_rejects_nonmonthly_period(self):
        valid, errors = self.build_and_validate(period="weekly", postpaid=True)
        self.assertFalse(valid)
        self.assertIn("billing_type", errors)

    def test_purchased_module_cannot_change_period(self):
        valid, errors = self.build_and_validate(
            existing=True, purchased=True, postpaid=True, period="weekly",
        )
        self.assertFalse(valid)
        self.assertIn("billing_type", errors)

    def test_purchased_module_cannot_change_postpaid_model(self):
        valid, errors = self.build_and_validate(
            existing=True, purchased=True, postpaid=False,
        )
        self.assertFalse(valid)
        self.assertIn("monthly_postpaid", errors)

    def test_metered_module_cannot_change_postpaid_model(self):
        valid, errors = self.build_and_validate(
            existing=True, purchased=False, metered=True, postpaid=False,
        )
        self.assertFalse(valid)
        self.assertIn("monthly_postpaid", errors)

    def test_purchased_module_can_save_price_with_same_model(self):
        valid, errors = self.build_and_validate(
            existing=True, purchased=True, postpaid=True,
        )
        self.assertTrue(valid, errors)
