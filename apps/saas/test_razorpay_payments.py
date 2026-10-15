import json
from unittest.mock import patch
from datetime import timedelta
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role
from .models import SubscriptionPlan, Subscription
from .payment_models import PaymentPolicy, SubscriptionPayment
from .payment_service import activate_captured, has_paid_access
from .payment_gateway import verify_checkout, verify_webhook


class RazorpaySubscriptionTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name="Paid Workshop",slug="paid-workshop")
        self.other=Company.objects.create(name="Other Workshop",slug="other-workshop")
        role=Role.objects.create(company=self.company,name="Owner",code="SUPER_ADMIN",permissions=["*"])
        self.owner=User.objects.create_user(company=self.company,role=role,name="Owner",
            email="billingowner@example.test",password="SecretPass123!")
        self.owner.company.owner = self.owner
        self.owner.company.save(update_fields=["owner"])
        self.plan=SubscriptionPlan.objects.create(key="pro-payment-test",name="Pro",
            monthly_price="999.00",yearly_price="9999.00")
        self.client=APIClient()
        self.client.force_authenticate(self.owner)

    def test_unpaid_account_is_blocked_when_enforced(self):
        policy=PaymentPolicy.current()
        policy.enforce_paywall=True
        policy.save()
        self.assertEqual(self.client.get("/api/v1/saas/payments/access").status_code,200)
        self.assertFalse(has_paid_access(self.company))
        # DRF test force_authenticate does not inject an HTTP Authorization
        # header for middleware, so actual bearer middleware enforcement
        # is separately covered below using real JWT.
        from rest_framework_simplejwt.tokens import AccessToken
        token=str(AccessToken.for_user(self.owner))
        blocked=APIClient().get("/api/v1/dashboard",HTTP_AUTHORIZATION="Bearer "+token)
        self.assertEqual(blocked.status_code,402)
        self.assertEqual(blocked.json()["code"],"SUBSCRIPTION_REQUIRED")

    @patch("apps.saas.payment_service.api_request")
    def test_captured_payment_idempotent_and_company_scoped(self, fetch):
        fetch.return_value={"status":"captured","order_id":"order_test001",
            "amount":99900,"currency":"INR","method":"upi"}
        pending=SubscriptionPayment.objects.create(company=self.company,order_id="order_test001",
            plan=self.plan,requested_by=self.owner,billing_cycle="monthly",amount_paise=99900)
        first=activate_captured("order_test001","pay_001")
        self.assertEqual(first.status,"captured")
        self.assertTrue(has_paid_access(self.company))
        self.assertFalse(has_paid_access(self.other))
        self.assertEqual(Subscription.objects.filter(company=self.company).count(),1)
        second=activate_captured("order_test001","pay_001")
        self.assertEqual(second.id,first.id)
        self.assertEqual(Subscription.objects.filter(company=self.company).count(),1)
        self.assertIsNotNone(second.access_until)
        self.assertEqual(self.client.get("/api/v1/saas/payments/history").data[0]["status"],"captured")

    @patch("apps.saas.payment_service.api_request")
    def test_wrong_amount_does_not_unlock(self, fetch):
        fetch.return_value={"status":"captured","order_id":"order_wrong",
            "amount":1,"currency":"INR"}
        SubscriptionPayment.objects.create(company=self.company,order_id="order_wrong",
            plan=self.plan,billing_cycle="monthly",amount_paise=99900)
        with self.assertRaises(ValueError):
            activate_captured("order_wrong","pay_wrong")
        self.assertFalse(has_paid_access(self.company))

    @patch("apps.saas.payment_views.api_request")
    @patch.dict("os.environ",{"RAZORPAY_KEY_ID":"rzp_test_dummy","RAZORPAY_KEY_SECRET":"testsecret"})
    def test_order_amount_is_set_only_from_plan(self, fetch):
        fetch.return_value={"id":"order_new01","amount":99900,"currency":"INR"}
        result=self.client.post("/api/v1/saas/payments/orders",
            {"planId":self.plan.id,"cycle":"monthly","amount":1},format="json")
        self.assertEqual(result.status_code,201)
        self.assertEqual(result.data["amount"],99900)
        self.assertEqual(SubscriptionPayment.objects.get(order_id="order_new01").amount_paise,99900)

    @patch.dict("os.environ",{"RAZORPAY_KEY_ID":"rzp_test_dummy",
        "RAZORPAY_KEY_SECRET":"super-secret", "RAZORPAY_WEBHOOK_SECRET":"webhook-secret"})
    def test_checkout_and_webhook_signatures(self):
        import hmac,hashlib
        order,payment="order_sig","pay_sig"
        signature=hmac.new(b"super-secret",f"{order}|{payment}".encode(),hashlib.sha256).hexdigest()
        self.assertTrue(verify_checkout(order,payment,signature))
        self.assertFalse(verify_checkout(order,payment,"invalid"))
        body=b'{"event":"payment.captured"}'
        signed=hmac.new(b"webhook-secret",body,hashlib.sha256).hexdigest()
        self.assertTrue(verify_webhook(body,signed))
        self.assertFalse(verify_webhook(body,"invalid"))
