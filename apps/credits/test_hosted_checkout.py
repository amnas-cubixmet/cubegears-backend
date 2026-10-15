"""Hosted Razorpay credit purchase: real-price, server-confirmed, idempotent."""
import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role
from .models import CreditPackage, CreditPurchase, CreditWallet, CreditLedger


@override_settings(
    RAZORPAY_KEY_ID="rzp_test_unit_only",
    RAZORPAY_KEY_SECRET="unit-only-private-key",
    RAZORPAY_WEBHOOK_SECRET="unit-only-webhook-key",
    FRONTEND_URL="http://localhost:5173",
)
class HostedCheckoutTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Hosted Workshop", slug="hosted-workshop")
        role = Role.objects.create(company=self.company, name="Billing Admin", code="ADMIN",
                                   permissions=["billing.view", "billing.edit"])
        self.user = User.objects.create_user(
            email="hosted-billing@example.test", name="Workshop Owner",
            password="StrongSecretTest123!", company=self.company, role=role,
        )
        self.package = CreditPackage.objects.create(
            name="70 Rupee Test Package", price_inr=Decimal("70.00"),
            credits=Decimal("140.00"), is_active=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = "/api/v1/saas/credits/checkout-links"

    def create_link(self):
        def remote(method, path, payload):
            self.assertEqual((method, path), ("POST", "payment_links"))
            self.assertEqual(payload["amount"], 7000)
            self.assertEqual(payload["accept_partial"], False)
            self.assertEqual(payload["callback_method"], "get")
            self.assertIn("/account/credits/buy?credit_purchase=", payload["callback_url"])
            return {
                "id": "plink_unit123", "reference_id": payload["reference_id"],
                "amount": 7000, "currency": "INR",
                "short_url": "https://rzp.io/i/unit123",
            }
        with patch("apps.credits.hosted_views.api_request", side_effect=remote):
            response = self.client.post(self.url, {"packageId": self.package.pk}, format="json")
        return response, CreditPurchase.objects.get()

    @staticmethod
    def payment():
        return {"id": "pay_unit123", "amount": 7000, "currency": "INR",
                "status": "captured", "order_id": "order_provider123"}

    @staticmethod
    def link(purchase):
        return {
            "id": purchase.payment_link_id, "reference_id": purchase.receipt,
            "amount": 7000, "amount_paid": 7000, "currency": "INR", "status": "paid",
            "payments": [{"payment_id": "pay_unit123", "status": "captured", "amount": 7000}],
        }

    def status(self, purchase):
        return self.client.get(f"{self.url}/{purchase.pk}/status")

    def test_creates_70_rupee_hosted_link_without_crediting_wallet(self):
        response, purchase = self.create_link()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["amount"], 7000)
        self.assertEqual(response.data["checkoutUrl"], "https://rzp.io/i/unit123")
        self.assertEqual(purchase.status, "pending")
        self.assertEqual(CreditLedger.objects.count(), 0)
        self.assertFalse(CreditWallet.objects.filter(company=self.company).exists())
        self.assertNotIn("unit-only-private-key", str(response.data))

    def test_captured_payment_credits_once_even_after_repeated_status_requests(self):
        _, purchase = self.create_link()
        with patch("apps.credits.hosted_checkout.fetch_link", return_value=self.link(purchase)):
            with patch("apps.credits.hosted_checkout.fetch_payment", return_value=self.payment()) as fetched:
                first = self.status(purchase)
                second = self.status(purchase)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.data["credited"])
        self.assertTrue(second.data["credited"])
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("140.00"))
        self.assertEqual(CreditLedger.objects.filter(company=self.company, kind="purchase").count(), 1)
        fetched.assert_called_once_with("pay_unit123")

    def test_unconfirmed_payment_never_credits(self):
        _, purchase = self.create_link()
        remote = {**self.link(purchase), "status": "created", "amount_paid": 0, "payments": None}
        with patch("apps.credits.hosted_checkout.fetch_link", return_value=remote):
            response = self.status(purchase)
        self.assertFalse(response.data["credited"])
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(CreditLedger.objects.count(), 0)

    def test_wrong_captured_amount_is_not_credited(self):
        _, purchase = self.create_link()
        invalid = {**self.payment(), "amount": 100}
        with patch("apps.credits.hosted_checkout.fetch_link", return_value=self.link(purchase)):
            with patch("apps.credits.hosted_checkout.fetch_payment", return_value=invalid):
                response = self.status(purchase)
        self.assertFalse(response.data["credited"])
        self.assertEqual(CreditLedger.objects.count(), 0)

    def test_signed_payment_link_webhook_credits_without_browser_return(self):
        _, purchase = self.create_link()
        payload = {"event": "payment_link.paid", "payload": {
            "payment_link": {"entity": {"id": purchase.payment_link_id}}
        }}
        raw = json.dumps(payload).encode()
        signature = hmac.new(b"unit-only-webhook-key", raw, hashlib.sha256).hexdigest()
        with patch("apps.credits.hosted_checkout.fetch_link", return_value=self.link(purchase)):
            with patch("apps.credits.hosted_checkout.fetch_payment", return_value=self.payment()):
                for _ in range(2):
                    result = self.client.post(
                        "/api/v1/saas/credits/webhook", raw, content_type="application/json",
                        HTTP_X_RAZORPAY_SIGNATURE=signature,
                        HTTP_X_RAZORPAY_EVENT_ID="event-hosted-payment-123",
                    )
                    self.assertEqual(result.status_code, 200)
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("140.00"))
        self.assertEqual(CreditLedger.objects.filter(kind="purchase").count(), 1)

    def test_other_company_cannot_read_payment(self):
        _, purchase = self.create_link()
        other = Company.objects.create(name="Other Company", slug="other-hosted")
        role = Role.objects.create(company=other, name="Admin", code="ADMIN",
                                   permissions=["billing.view", "billing.edit"])
        stranger = User.objects.create_user(
            email="other-billing@example.test", name="Other Admin",
            password="StrongSecretTest123!", company=other, role=role,
        )
        self.client.force_authenticate(stranger)
        self.assertEqual(self.status(purchase).status_code, 404)

    def test_invalid_provider_checkout_host_fails_closed(self):
        with patch("apps.credits.hosted_views.api_request", return_value={
            "id": "plink_danger", "reference_id": "irrelevant",
            "amount": 7000, "currency": "INR",
            "short_url": "https://evil.example.test/payment",
        }):
            response = self.client.post(self.url, {"packageId": self.package.pk}, format="json")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(CreditPurchase.objects.count(), 0)
