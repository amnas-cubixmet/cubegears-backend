import hashlib
import hmac
import json
from decimal import Decimal
from datetime import timedelta, datetime, time
from io import StringIO
from django.core import mail
from django.core.management import call_command
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role
from .models import SubscriptionPlan, RazorpayPlanPayment, Subscription, SubscriptionExpiryReminder
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
        self.user.company.owner = self.user
        self.user.company.save(update_fields=["owner"])
        self.plan = SubscriptionPlan.objects.create(
            key="billing-monthly-plan", name="Workshop Pro",
            monthly_price=Decimal("499.00"), yearly_price=Decimal("3999.00"),
            checkout_price=Decimal("499.00"),
            access_duration_days=180, is_active=True, is_public=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_checkout(self):
        with patch("apps.saas.checkout_views.create_order", return_value={
            "id": "order_abc123", "amount": 49900, "currency": "INR",
        }) as mocked:
            reply = self.client.post("/api/v1/saas/checkout/orders", {
                "plan_id": self.plan.pk, "amount_paise": 2, "validity_days": 1
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

    def test_unpaid_account_can_access_dashboard_when_role_allows(self):
        # Credit-wallet billing replaced the old subscription paywall. Only
        # tenant RBAC and (if enabled) paid feature entitlements gate access.
        role = self.user.role
        role.permissions = [*role.permissions, "dashboard.view"]
        role.save(update_fields=["permissions"])
        jwt_client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        jwt_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        result = jwt_client.get("/api/v1/dashboard")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(jwt_client.get("/api/v1/saas/checkout/status").status_code, 200)
        # The legacy subscription status remains queryable for historical data,
        # but it does not restrict the credit-based dashboard.
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
        refund = {"event": "refund.processed", "payload": {
            "payment": {"entity": {**self.captured(), "amount_refunded": 49900}},
            "refund": {"entity": {"payment_id": "pay_abc123", "status": "processed", "amount": 49900}},
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
        other_user.company.owner = other_user
        other_user.company.save(update_fields=["owner"])
        self.client.force_authenticate(other_user)
        self.assertEqual(self.client.get("/api/v1/saas/checkout/history").data, [])
        self.assertEqual(
            self.client.get("/api/v1/saas/checkout/orders/order_abc123").status_code, 404
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_renewal_reminders_are_sent_once_to_company_email(self):
        self.company.email = "billing-company@example.test"
        self.company.save(update_fields=["email"])
        self.create_checkout()
        with patch("apps.saas.checkout_views.fetch_payment", return_value=self.captured()):
            response = self.client.post("/api/v1/saas/checkout/verify", {
                "razorpay_order_id": "order_abc123",
                "razorpay_payment_id": "pay_abc123",
                "razorpay_signature": self.signature(),
            }, format="json")
        self.assertEqual(response.status_code, 200)
        sub = Subscription.objects.get(company=self.company)
        expires = timezone.make_aware(
            datetime.combine(timezone.localdate() + timedelta(days=7), time(23, 59)),
            timezone.get_current_timezone(),
        )
        sub.renews_at = expires
        sub.save(update_fields=["renews_at"])
        call_command("send_subscription_expiry_reminders", dry_run=True, stdout=StringIO())
        self.assertEqual(SubscriptionExpiryReminder.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)
        call_command("send_subscription_expiry_reminders", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["billing-company@example.test"])
        self.assertIn("7 day(s)", mail.outbox[0].body)
        call_command("send_subscription_expiry_reminders", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(SubscriptionExpiryReminder.objects.count(), 1)

    def test_unpriced_plan_cannot_create_paid_order(self):
        self.plan.checkout_price = Decimal("0")
        self.plan.save(update_fields=["checkout_price"])
        response = self.client.post("/api/v1/saas/checkout/orders", {
            "plan_id": self.plan.pk,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(RazorpayPlanPayment.objects.count(), 0)

    def test_expired_legacy_plan_does_not_block_credit_based_dashboard(self):
        self.create_checkout()
        with patch("apps.saas.checkout_views.fetch_payment", return_value=self.captured()):
            answer = self.client.post("/api/v1/saas/checkout/verify", {
                "razorpay_order_id": "order_abc123",
                "razorpay_payment_id": "pay_abc123",
                "razorpay_signature": self.signature(),
            }, format="json")
        self.assertEqual(answer.status_code, 200)
        yesterday = timezone.now() - timedelta(days=1)
        sub = Subscription.objects.get(company=self.company)
        sub.renews_at = yesterday
        sub.save(update_fields=["renews_at"])
        order = RazorpayPlanPayment.objects.get(company=self.company)
        order.period_end = yesterday
        order.save(update_fields=["period_end"])
        self.assertFalse(access_state(self.company)["allowed"])
        self.assertEqual(access_state(self.company)["status"], "expired")
        jwt = APIClient()
        jwt.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.user).access_token}")
        role = self.user.role
        role.permissions = [*role.permissions, "dashboard.view"]
        role.save(update_fields=["permissions"])
        self.assertEqual(jwt.get("/api/v1/dashboard").status_code, 200)

    def test_non_billing_staff_cannot_read_same_tenant_order_details(self):
        self.create_checkout()
        role = Role.objects.create(
            company=self.company, name="Mechanic", code="MECHANIC",
            permissions=["jobs.view"],
        )
        staff = User.objects.create_user(
            email="staff-checkout@example.test", name="Mechanic",
            password="CheckoutSecure371!", company=self.company, role=role,
        )
        self.client.force_authenticate(staff)
        self.assertEqual(
            self.client.get("/api/v1/saas/checkout/orders/order_abc123").status_code,
            403,
        )
        self.assertEqual(self.client.get("/api/v1/saas/checkout/history").status_code, 403)
