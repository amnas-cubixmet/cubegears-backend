import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role
from .models import SubscriptionPlan, RazorpayPlanPayment, Subscription
from .billing_service import access_state


@override_settings(
    RAZORPAY_KEY_ID="rzp_test_checkout",
    RAZORPAY_KEY_SECRET="unit-test-key-secret",
    RAZORPAY_WEBHOOK_SECRET="unit-test-webhook-secret",
    SAAS_PAYMENT_ENFORCEMENT_ENABLED=True,
)
class RazorpayBillingTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Workshop One", slug="payment-workshop-one")
        self.foreign = Company.objects.create(name="Workshop Two", slug="payment-workshop-two")
        role = Role.objects.create(
            company=self.company, name="Billing Admin", code="ADMIN",
            permissions=["billing.view", "billing.edit"],
        )
        self.user = User.objects.create_user(
            email="checkout-admin@example.test", name="Billing Owner",
            password="CheckoutSecure371!", company=self.company, role=role,
        )
        self.plan = SubscriptionPlan.objects.create(
            key="billing-monthly-plan", name="Workshop Pro",
            monthly_price=Decimal("499.00"), yearly_price=Decimal("3999.00"),
            access_duration_days=180, is_active=True, is_public=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_checkout(self):
        with patch("apps.saas.checkout_views.create_order", return_value={
            "id": "order_abc123", "amount": 49900, "currency": "INR",
        }) as mocked:
            reply = self.client.post("/api/v1/saas/checkout/orders", {
                "plan_id": self.plan.pk, "billing_cycle": "monthly"
            }, format="json")
        return reply, mocked

    @staticmethod
    def signature():
        return hmac.new(
            b"unit-test-key-secret", b"order_abc123|pay_abc123", hashlib.sha256
        ).hexdigest()

    @staticmethod
    def captured():
        return {
            "id": "pay_abc123", "order_id": "order_abc123",
            "amount": 49900, "currency": "INR",
            "status": "captured", "method": "upi",
        }

    def test_unpaid_account_cannot_access_dashboard_api(self):
        jwt_client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        jwt_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        result = jwt_client.get("/api/v1/dashboard")
        self.assertEqual(result.status_code, 402)
        self.assertEqual(result.json()["code"], "SUBSCRIPTION_PAYMENT_REQUIRED")
        self.assertEqual(jwt_client.get("/api/v1/saas/checkout/status").status_code, 200)
        self.assertFalse(access_state(self.company)["allowed"])

    def test_checkout_never_accepts_client_supplied_price_or_validity(self):
        result, mock_create = self.create_checkout()
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.data["amount"], 49900)
        self.assertEqual(result.data["validityDays"], 180)
        self.assertNotIn("unit-test-key-secret", str(result.data))
        record = RazorpayPlanPayment.objects.get(company=self.company)
        self.assertEqual(record.status, "pending")
        self.assertEqual(record.amount_paise, 49900)
        self.assertEqual(record.validity_days, 180)
        self.assertEqual(Subscription.objects.filter(company=self.company).count(), 0)
        mock_create.assert_called_once()

    def test_verified_captured_payment_unlocks_once(self):
        result, _ = self.create_checkout()
        self.assertEqual(result.status_code, 201)
        response_body = {
            "razorpay_order_id": "order_abc123",
            "razorpay_payment_id": "pay_abc123",
            "razorpay_signature": self.signature(),
        }
        with patch("apps.saas.checkout_views.fetch_payment", return_value=self.captured()):
            first = self.client.post("/api/v1/saas/checkout/verify", response_body, format="json")
            second = self.client.post("/api/v1/saas/checkout/verify", response_body, format="json")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(access_state(self.company)["allowed"])
        self.assertEqual(Subscription.objects.filter(company=self.company).count(), 1)
        sub = Subscription.objects.get(company=self.company)
        self.assertGreater(sub.renews_at, timezone.now())

    def test_forged_checkout_or_mismatched_captured_amount_does_not_unlock(self):
        self.create_checkout()
        bad = self.client.post("/api/v1/saas/checkout/verify", {
            "razorpay_order_id": "order_abc123",
            "razorpay_payment_id": "pay_abc123",
            "razorpay_signature": "invalid",
        }, format="json")
        self.assertEqual(bad.status_code, 400)
        incorrect = {**self.captured(), "amount": 10}
        with patch("apps.saas.checkout_views.fetch_payment", return_value=incorrect):
            response = self.client.post("/api/v1/saas/checkout/verify", {
                "razorpay_order_id": "order_abc123",
                "razorpay_payment_id": "pay_abc123",
                "razorpay_signature": self.signature(),
            }, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(access_state(self.company)["allowed"])
        self.assertEqual(Subscription.objects.filter(company=self.company).count(), 0)

    def test_signed_webhook_captured_is_idempotent_and_refund_revokes(self):
        self.create_checkout()
        payload = {"event": "payment.captured", "payload": {
            "payment": {"entity": self.captured()},
        }}
        raw = json.dumps(payload).encode()
        signature = hmac.new(b"unit-test-webhook-secret", raw, hashlib.sha256).hexdigest()
        for _ in range(2):
            result = self.client.post(
                "/api/v1/saas/checkout/webhook", raw, content_type="application/json",
                HTTP_X_RAZORPAY_SIGNATURE=signature,
                HTTP_X_RAZORPAY_EVENT_ID="event-captured-123",
            )
            self.assertEqual(result.status_code, 200)
        self.assertEqual(Subscription.objects.filter(company=self.company).count(), 1)
        refund = {"event": "payment.refunded", "payload": {
            "payment": {"entity": {**self.captured(), "amount_refunded": 49900}},
        }}
        raw_refund = json.dumps(refund).encode()
        refund_signature = hmac.new(b"unit-test-webhook-secret", raw_refund, hashlib.sha256).hexdigest()
        revoked = self.client.post(
            "/api/v1/saas/checkout/webhook", raw_refund, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=refund_signature,
            HTTP_X_RAZORPAY_EVENT_ID="event-refunded-123",
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertFalse(access_state(self.company)["allowed"])
        self.assertEqual(RazorpayPlanPayment.objects.get().status, "refunded")

    def test_tenant_payment_history_is_isolated(self):
        self.create_checkout()
        other_role = Role.objects.create(company=self.foreign, name="Billing Owner",
                                         code="ADMIN", permissions=["billing.view", "billing.edit"])
        other_user = User.objects.create_user(
            email="other-checkout@example.test", name="Other Owner",
            password="CheckoutSecure371!", company=self.foreign, role=other_role,
        )
        self.client.force_authenticate(other_user)
        self.assertEqual(self.client.get("/api/v1/saas/checkout/history").data, [])
        self.assertEqual(
            self.client.get("/api/v1/saas/checkout/orders/order_abc123").status_code, 404
        )
