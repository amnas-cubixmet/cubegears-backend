"""Regression tests for the platform credit package creation form."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.credits.forms import CreditPackageForm
from apps.credits.models import CreditPackage


class CreditPackageDisplayOrderTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            email="package-admin@example.test", name="Package Admin",
            password="Test-strong-password-2026!",
        )
        self.client.force_login(self.superuser)
        self.url = reverse("panel:credits")

    def post_package(self, **kwargs):
        payload = {
            "action": "package",
            "name": "Starter",
            "credits": "500.00",
            "price_inr": "500.00",
            "is_active": "on",
        }
        payload.update(kwargs)
        return self.client.post(self.url, payload)

    def test_blank_display_order_creates_package_and_automatically_orders(self):
        first = self.post_package()
        self.assertEqual(first.status_code, 302)
        self.assertEqual(CreditPackage.objects.get(name="Starter").sort_order, 0)

        second = self.post_package(name="Plus", sort_order="")
        self.assertEqual(second.status_code, 302)
        self.assertEqual(CreditPackage.objects.get(name="Plus").sort_order, 1)

    def test_explicit_large_order_is_accepted_without_artificial_ui_cap(self):
        saved = self.post_package(sort_order="1000000")
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(CreditPackage.objects.get().sort_order, 1000000)

    def test_blank_order_when_editing_preserves_old_value(self):
        original = CreditPackage.objects.create(
            name="Existing", credits=Decimal("100"), price_inr=Decimal("100"),
            sort_order=777,
        )
        form = CreditPackageForm(
            data={
                "name": "Existing", "credits": "100", "price_inr": "100",
                "sort_order": "", "is_active": "on",
            },
            instance=original,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().sort_order, 777)

    def test_zero_price_is_still_invalid_for_razorpay(self):
        response = self.post_package(price_inr="0", sort_order="")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CreditPackage.objects.exists())
        rendered = response.content.decode()
        self.assertIn("Razorpay packages must cost at least INR 1.", rendered)
        self.assertNotIn("&lt;ul class=&quot;errorlist&quot;", rendered)

    def test_negative_display_order_is_invalid(self):
        invalid = CreditPackageForm(data={
            "name": "Invalid", "credits": "100", "price_inr": "100",
            "sort_order": "-1", "is_active": "on",
        })
        self.assertFalse(invalid.is_valid())
        self.assertIn("sort_order", invalid.errors)

    def test_form_and_table_explain_optional_order(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Display order (optional)")
        self.assertContains(response, 'placeholder="Automatic"')
