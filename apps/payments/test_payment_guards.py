from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from .views import PaymentViewSet


class InvoicePaymentPostingTests(TestCase):
    def setUp(self):
        self.company = SimpleNamespace(id="company-1")
        self.customer = SimpleNamespace(id="customer-1", company_id=self.company.id)
        self.invoice_ref = SimpleNamespace(
            pk="invoice-1", company_id=self.company.id, customer_id=self.customer.id
        )
        self.locked_invoice = SimpleNamespace(
            kind="invoice", status="Finalized",
            total=Decimal("100.00"), paid=Decimal("30.00"),
            balance=Decimal("70.00"), save=Mock()
        )
        self.view = PaymentViewSet()
        self.view.request = SimpleNamespace(
            user=SimpleNamespace(company=self.company, branch=None)
        )

    def post(self, amount):
        amount = Decimal(amount)
        serializer = Mock()
        serializer.validated_data = {
            "customer": self.customer,
            "invoice": self.invoice_ref,
            "amount": amount,
        }
        serializer.save.return_value = SimpleNamespace(
            invoice_id=self.invoice_ref.pk, amount=amount
        )
        with patch("apps.payments.views.Invoice.objects.select_for_update") as locked:
            locked.return_value.get.return_value = self.locked_invoice
            self.view.perform_create(serializer)
            return serializer, locked

    def test_pay_remaining_balance(self):
        serializer, locked = self.post("70.00")
        locked.return_value.get.assert_called_once_with(
            pk=self.invoice_ref.pk, company=self.company
        )
        serializer.save.assert_called_once()
        self.assertEqual(self.locked_invoice.paid, Decimal("100.00"))
        self.assertEqual(self.locked_invoice.balance, Decimal("0"))
        self.assertEqual(self.locked_invoice.status, "Paid")

    def test_reject_overpayment(self):
        with self.assertRaises(ValidationError):
            self.post("71.00")
        self.locked_invoice.save.assert_not_called()

    def test_reject_zero_payment(self):
        with self.assertRaises(ValidationError):
            self.post("0.00")
        self.locked_invoice.save.assert_not_called()

    def test_reject_cancelled_invoice(self):
        self.locked_invoice.status = "Cancelled"
        with self.assertRaises(ValidationError):
            self.post("20.00")
        self.locked_invoice.save.assert_not_called()

    def test_partial_payment_remains_open(self):
        self.post("20.00")
        self.assertEqual(self.locked_invoice.paid, Decimal("50.00"))
        self.assertEqual(self.locked_invoice.balance, Decimal("50.00"))
        self.assertEqual(self.locked_invoice.status, "Finalized")
