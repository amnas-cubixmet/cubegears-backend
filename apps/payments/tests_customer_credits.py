"""Part 11 live REST scenarios: partial payments, advances, allocations and refunds."""
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.finance.views import account_balance
from apps.inventory.models import WorkshopAccountEntry, WorkshopFinancialAccount
from apps.invoices.models import Invoice
from apps.payments.models import Payment, CustomerCreditAllocation
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False, CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class CustomerAdvanceAndPaymentTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Advance Garage", slug="part11-advance-garage")
        role = Role.objects.create(
            company=self.company, name="Owner", code="PART11_OWNER",
            permissions=["*"], is_system=True,
        )
        self.owner = User.objects.create_user(
            email="part11-owner@example.test", name="Owner",
            password="TestStrongPassword123", company=self.company, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(
            company=self.company, name="Amal", phone="+919554444444",
        )
        self.invoice = Invoice.objects.create(
            company=self.company, customer=self.customer, number="PART11-INV-001",
            kind="invoice", status="Finalized", finalized_at=timezone.now(),
            date=timezone.localdate(), total=Decimal("1000.00"),
            paid=Decimal("0.00"), balance=Decimal("1000.00"),
        )
        self.account = WorkshopFinancialAccount.objects.create(
            company=self.company, name="Part11 Cash",
            kind="Cash", opening_balance=Decimal("0"), created_by=self.owner,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def advance(self, *, amount="500.00", key="advance-first"):
        return self.client.post("/api/v1/payments", {
            "customer": str(self.customer.pk),
            "date": timezone.localdate().isoformat(),
            "amount": amount, "method": "Cash",
            "direction": "IN", "account": str(self.account.pk),
            "idempotency_key": key,
        }, format="json")

    def apply(self, advance_id, *, amount="300.00", key="advance-apply", invoice_id=None):
        return self.client.post(f"/api/v1/payments/{advance_id}/apply-advance", {
            "invoiceId": str(invoice_id or self.invoice.pk),
            "amount": amount, "idempotency_key": key,
        }, format="json")

    def test_advance_posts_once_without_creating_invoice_revenue(self):
        created = self.advance()
        self.assertEqual(created.status_code, 201, created.data)
        self.assertIsNone(Payment.objects.get(pk=created.data["id"]).invoice_id)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal("0"))
        self.assertEqual(account_balance(self.account), Decimal("500.00"))
        self.assertEqual(WorkshopAccountEntry.objects.filter(company=self.company).count(), 1)
        repeated = self.advance()
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(created.data["id"], repeated.data["id"])
        self.assertEqual(Payment.objects.filter(company=self.company).count(), 1)
        self.assertEqual(WorkshopAccountEntry.objects.filter(company=self.company).count(), 1)
        bad = self.advance(amount="600.00")
        self.assertEqual(bad.status_code, 400)

    def test_partial_invoice_receipt_and_double_click_do_not_overpost(self):
        url = "/api/v1/payments"
        data = {
            "customer": str(self.customer.pk), "invoice": str(self.invoice.pk),
            "date": timezone.localdate().isoformat(), "amount": "250.00",
            "method": "UPI", "account": str(self.account.pk),
            "idempotency_key": "partial-250",
        }
        first = self.client.post(url, data, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        second = self.client.post(url, data, format="json")
        self.assertEqual(second.status_code, 200, second.data)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "Partially Paid")
        self.assertEqual(self.invoice.paid, Decimal("250.00"))
        self.assertEqual(self.invoice.balance, Decimal("750.00"))
        self.assertEqual(account_balance(self.account), Decimal("250.00"))
        self.assertEqual(Payment.objects.filter(company=self.company).count(), 1)
        excess = {**data, "idempotency_key": "overpayment", "amount": "800.00"}
        self.assertEqual(self.client.post(url, excess, format="json").status_code, 400)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal("250.00"))

    def test_apply_advance_reduces_invoice_due_without_new_cash_entry(self):
        advance = self.advance().data["id"]
        applied = self.apply(advance)
        self.assertEqual(applied.status_code, 201, applied.data)
        self.assertEqual(applied.data["invoiceBalance"], "700.00")
        self.assertEqual(applied.data["advanceAvailable"], "200.00")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal("300.00"))
        self.assertEqual(self.invoice.status, "Partially Paid")
        self.assertEqual(account_balance(self.account), Decimal("500.00"))
        self.assertEqual(WorkshopAccountEntry.objects.filter(company=self.company).count(), 1)
        repeated = self.apply(advance)
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(CustomerCreditAllocation.objects.count(), 1)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal("300.00"))
        self.assertEqual(self.apply(advance, amount="300.00", key="too-much").status_code, 400)

    def test_refund_advance_is_cash_out_and_reversal_restores_credit(self):
        advance = self.advance().data["id"]
        self.assertEqual(self.apply(advance).status_code, 201)
        url = f"/api/v1/payments/{advance}/refund-advance"
        payload = {
            "amount": "100.00", "accountId": str(self.account.pk),
            "method": "Cash", "reason": "Customer cancelled prepaid service",
            "idempotency_key": "advance-refund-100",
        }
        result = self.client.post(url, payload, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data["advanceAvailable"], "100.00")
        self.assertEqual(account_balance(self.account), Decimal("400.00"))
        repeat = self.client.post(url, payload, format="json")
        self.assertEqual(repeat.status_code, 200, repeat.data)
        self.assertEqual(account_balance(self.account), Decimal("400.00"))
        self.assertEqual(self.client.post(
            url, {**payload, "amount": "250.00", "idempotency_key": "too-large-refund"},
            format="json",
        ).status_code, 400)
        reverse = self.client.post(
            f"/api/v1/payments/{result.data['refundId']}/reverse",
            {"reason": "Refund rejected by cash desk"}, format="json",
        )
        self.assertEqual(reverse.status_code, 200, reverse.data)
        self.assertEqual(account_balance(self.account), Decimal("500.00"))
        ledger = self.client.get(f"/api/v1/customers/{self.customer.pk}/ledger")
        self.assertEqual(ledger.data["advanceAvailable"], "200.00")
        self.assertEqual(ledger.data["advanceRefunded"], "0.00")

    def test_credit_allocation_reverse_reopens_invoice_and_advance(self):
        advance = self.advance().data["id"]
        allocation_id = self.apply(advance).data["applicationId"]
        reversed_payment = self.client.post(
            f"/api/v1/payments/{advance}/reverse",
            {"reason": "Receipt entered in error"}, format="json",
        )
        self.assertEqual(reversed_payment.status_code, 400)
        url = f"/api/v1/payments/{advance}/reverse-advance-application"
        params = {"applicationId": allocation_id, "reason": "Credit belongs to a different invoice"}
        result = self.client.post(url, params, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["advanceAvailable"], "500.00")
        self.assertEqual(self.client.post(url, params, format="json").status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal("0.00"))
        self.assertEqual(self.invoice.balance, Decimal("1000.00"))
        self.assertEqual(self.invoice.status, "Finalized")
        self.assertEqual(account_balance(self.account), Decimal("500.00"))
        undone = self.client.post(
            f"/api/v1/payments/{advance}/reverse",
            {"reason": "Receipt entered in error"}, format="json",
        )
        self.assertEqual(undone.status_code, 200, undone.data)
        self.assertEqual(account_balance(self.account), Decimal("0.00"))

    def test_ledger_reports_real_receivables_credit_and_applications(self):
        advance = self.advance().data["id"]
        self.apply(advance)
        ledger = self.client.get(f"/api/v1/customers/{self.customer.pk}/ledger")
        self.assertEqual(ledger.status_code, 200, ledger.data)
        self.assertEqual(ledger.data["totalInvoiced"], "1000.00")
        self.assertEqual(ledger.data["invoiceOutstanding"], "700.00")
        self.assertEqual(ledger.data["advanceReceived"], "500.00")
        self.assertEqual(ledger.data["advanceApplied"], "300.00")
        self.assertEqual(ledger.data["advanceAvailable"], "200.00")
        self.assertEqual(ledger.data["netExposureAfterAvailableCredit"], "500.00")
        self.assertEqual({x["type"] for x in ledger.data["entries"]},
                         {"invoice", "advance", "credit_applied"})
        self.assertEqual(self.client.get(f"/api/v1/customers/{self.customer.pk}/outstanding").data["outstanding"], Decimal("700.00"))

    def test_reject_tenant_crossing_and_wrong_customer(self):
        another = Company.objects.create(name="Other Credit Garage", slug="part11-other")
        other_customer = Customer.objects.create(
            company=another, name="Another", phone="+919223344556",
        )
        other_invoice = Invoice.objects.create(
            company=another, customer=other_customer,
            number="FOREIGN11", kind="invoice", status="Finalized",
            date=timezone.localdate(), finalized_at=timezone.now(),
            total=Decimal("100.00"), balance=Decimal("100.00"),
        )
        advance = self.advance().data["id"]
        self.assertEqual(self.apply(advance, invoice_id=other_invoice.pk).status_code, 400)
        self.assertEqual(self.client.get(f"/api/v1/customers/{other_customer.pk}/ledger").status_code, 404)
        self.assertEqual(self.client.get(
            f"/api/v1/payments/advances?customerId={other_customer.pk}",
        ).status_code, 400)
        self.assertEqual(CustomerCreditAllocation.objects.count(), 0)

    def test_staff_must_have_both_invoice_and_payment_grants_for_ledger(self):
        role = Role.objects.create(
            company=self.company, name="Payment Clerk", code="PART11_CLERK",
            permissions=["payments.view"],
        )
        clerk = User.objects.create_user(
            email="part11-clerk@example.test", name="Clerk",
            password="TestStrongPassword123", company=self.company, role=role,
        )
        self.client.force_authenticate(clerk)
        self.assertEqual(
            self.client.get(f"/api/v1/customers/{self.customer.pk}/ledger").status_code,
            403,
        )
        self.assertEqual(
            self.client.post("/api/v1/payments", {
                "customer": str(self.customer.pk),
                "date": timezone.localdate().isoformat(),
                "amount": "50", "account": str(self.account.pk),
                "idempotency_key": "not-allowed",
            }, format="json").status_code,
            403,
        )
