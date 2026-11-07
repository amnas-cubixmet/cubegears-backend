"""Immutable customer collections: reverse receipt and invoice balance together."""
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.invoices.models import Invoice
from apps.inventory.models import WorkshopFinancialAccount
from apps.payments.models import Payment
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class CustomerPaymentReversalTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Payment Test Garage", slug="payment-test-garage")
        self.role = Role.objects.create(
            company=self.company, name="Collection Operator", code="COLLECTION_OPERATOR",
            permissions=["payments.view", "payments.create", "payments.edit", "payments.delete"],
        )
        self.staff = User.objects.create_user(
            email="receipt-operator@example.test", password="temporary-safe-password",
            name="Cashier", company=self.company, role=self.role,
        )
        self.owner_role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER", is_system=True, permissions=["*"],
        )
        self.owner = User.objects.create_user(
            email="receipt-owner@example.test", password="temporary-safe-password",
            name="Owner", company=self.company, role=self.owner_role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.cash = WorkshopFinancialAccount.objects.create(
            company=self.company, name="Collection Cash", kind="Cash",
            opening_balance=Decimal("0.00"), created_by=self.owner,
        )
        self.customer = Customer.objects.create(
            company=self.company, name="Rahul", phone="+919876543210",
        )
        self.invoice = Invoice.objects.create(
            company=self.company, customer=self.customer, number="PAY-INV-1",
            kind="invoice", status="Finalized", finalized_at=timezone.now(),
            date="2026-11-05",
            total=Decimal("1000"), balance=Decimal("1000"),
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def create_payment(self):
        result = self.client.post("/api/v1/payments", {
            "customer": str(self.customer.pk), "invoice": str(self.invoice.pk),
            "date": "2026-11-05", "amount": "1000.00",
            "method": "Cash", "account": str(self.cash.pk), "status": "Completed",
        }, format="json")
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal("1000.00"))
        self.assertEqual(self.invoice.balance, Decimal("0.00"))
        self.assertEqual(self.invoice.status, "Paid")
        return result.data["id"]

    def test_paid_receipt_cannot_be_mutated_or_deleted(self):
        pk = self.create_payment()
        self.assertEqual(self.client.patch(f"/api/v1/payments/{pk}", {"amount": "1"}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v1/payments/{pk}").status_code, 403)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal("1000"))
        self.assertEqual(Payment.objects.count(), 1)

    def test_reverse_is_atomic_idempotent_and_audited(self):
        pk = self.create_payment()
        url = f"/api/v1/payments/{pk}/reverse"
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 400)
        result = self.client.post(url, {"reason": "Duplicate cashier receipt"}, format="json")
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))
        payment = Payment.objects.get(pk=pk)
        self.assertEqual(payment.status, "Reversed")
        self.assertEqual(payment.reversal_reason, "Duplicate cashier receipt")
        self.assertEqual(payment.reversed_by, self.owner)
        self.assertIsNotNone(payment.reversed_at)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal("0"))
        self.assertEqual(self.invoice.balance, Decimal("1000"))
        self.assertEqual(self.invoice.status, "Finalized")
        self.assertEqual(self.client.post(url, {"reason": "Repeated request"}, format="json").status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.balance, Decimal("1000"))
        payment.refresh_from_db()
        self.assertEqual(payment.reversal_reason, "Duplicate cashier receipt")

    def test_cashier_with_legacy_edit_cannot_reverse_without_new_explicit_code(self):
        pk = self.create_payment()
        self.client.force_authenticate(user=self.staff)
        self.assertEqual(
            self.client.post(f"/api/v1/payments/{pk}/reverse", {"reason": "Correction"}, format="json").status_code,
            403,
        )
        self.role.permissions += ["payments.reverse"]
        self.role.save(update_fields=["permissions"])
        self.staff.role = self.role
        result = self.client.post(f"/api/v1/payments/{pk}/reverse", {"reason": "Correction"}, format="json")
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))

    def test_cannot_create_fake_reversed_receipt(self):
        response = self.client.post("/api/v1/payments", {
            "customer": str(self.customer.pk), "invoice": str(self.invoice.pk),
            "date": "2026-11-05", "amount": "100.00", "status": "Reversed",
        }, format="json")
        self.assertEqual(response.status_code, 400, getattr(response, "data", None))
        self.assertEqual(Payment.objects.count(), 0)
