"""Outside shop parts remain approval-gated and receive one financial posting."""
from decimal import Decimal
from uuid import uuid4
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.jobs.models import Job
from apps.vehicles.models import Vehicle
from apps.roles.models import Role
from apps.inventory.models import WorkshopFinancialAccount, WorkshopAccountEntry
from apps.finance.views import account_balance
from apps.expenses.models import Expense, ExpenseSettlement
from apps.invoices.job_draft import suggested_job_invoice


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
                   CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class OutsidePartPaymentTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Outside Garage", slug="outside-garage")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="OUT")
        role = Role.objects.create(company=self.company, name="Owner", code="OUTSIDE_OWNER",
                                   permissions=["*"], is_active=True)
        self.owner = User.objects.create_user(email="outside-owner@example.test",
                                              password="test-pass", name="Owner",
                                              company=self.company, branch=self.branch, role=role)
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        customer = Customer.objects.create(company=self.company, branch=self.branch,
                                           name="Customer", phone="9876543209")
        vehicle = Vehicle.objects.create(company=self.company, branch=self.branch,
                                         customer=customer, registration="KL10AB9876",
                                         make="Toyota", model="Innova")
        self.job = Job.objects.create(company=self.company, branch=self.branch,
                                      job_number="OUTSIDE-TEST-1", customer=customer,
                                      vehicle=vehicle, status=Job.STATUS_IN_PROGRESS)
        self.bank = WorkshopFinancialAccount.objects.create(
            company=self.company, branch=self.branch, name="Bank A", kind="Bank",
            opening_balance=Decimal("2500.00"), created_by=self.owner)
        self.cash = WorkshopFinancialAccount.objects.create(
            company=self.company, branch=self.branch, name="Cash A", kind="Cash",
            opening_balance=Decimal("800.00"), created_by=self.owner)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = f"/api/v1/job-cards/{self.job.pk}/outside-parts"

    def request_quote(self):
        response = self.client.post(self.url, {
            "requestId": str(uuid4()), "partName": "Brake Pads",
            "vendor": "Corner Parts", "quantity": "2",
            "unitCost": "100", "sellingPrice": "180", "purchaseGstRate": "18",
        }, format="json")
        self.assertEqual(response.status_code, 201, getattr(response, "data", None))
        self.assertEqual(response.data["quotedTotal"], "236.00")
        self.assertEqual(response.data["quotedGstAmount"], "36.00")
        return response.data["id"]

    def approve(self, pid):
        result = self.client.post(self.url + f"/{pid}/decision",
                                  {"decision": "approve"}, format="json")
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))

    def complete(self, pid, payment_type="Credit", account=None, unit_cost="100"):
        doc = SimpleUploadedFile("invoice.pdf",
                                 b"%PDF-1.4\n1 0 obj\nendobj\n%%EOF\n",
                                 content_type="application/pdf")
        values = {"invoiceNo": "SUP-99", "unitCost": unit_cost, "bill": doc,
                  "paymentType": payment_type}
        if account is not None:
            values["accountId"] = str(account.pk)
        return self.client.post(self.url + f"/{pid}/complete", values, format="multipart")

    def test_bank_payment_debits_once_and_customer_invoice_uses_selling_rate(self):
        pid = self.request_quote()
        self.approve(pid)
        before = account_balance(self.bank)
        purchased = self.complete(pid, "Bank", self.bank)
        self.assertEqual(purchased.status_code, 200, getattr(purchased, "data", None))
        self.assertEqual(purchased.data["totalCost"], "236.00")
        self.assertEqual(purchased.data["totalSelling"], "360.00")
        self.assertEqual(purchased.data["purchaseGstAmount"], "36.00")
        self.assertEqual(purchased.data["paymentStatus"], "Paid")
        self.assertEqual(before - account_balance(self.bank), Decimal("236.00"))
        expense = Expense.objects.get(pk=purchased.data["expenseId"])
        self.assertEqual(expense.amount, Decimal("236.00"))
        self.assertEqual(expense.tax, Decimal("36.00"))
        self.assertEqual(expense.status, "Paid")
        self.assertEqual(ExpenseSettlement.objects.filter(expense=expense).count(), 1)
        self.assertEqual(WorkshopAccountEntry.objects.filter(
            company=self.company, entry_type="expense_payment").count(), 1)
        repeated = self.complete(pid, "Bank", self.bank)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(before - account_balance(self.bank), Decimal("236.00"))
        self.assertEqual(Expense.objects.filter(company=self.company).count(), 1)
        self.job.refresh_from_db()
        suggested = suggested_job_invoice(self.job)
        outside = next(line for line in suggested["items"]
                       if line["type"] == "Outside Purchase")
        self.assertEqual(outside["rate"], "180.00")
        self.assertEqual(outside["qty"], "2")
        self.assertNotIn("100.00", str(outside))

    def test_credit_creates_unpaid_expense_with_no_cash_debit(self):
        pid = self.request_quote()
        self.approve(pid)
        before = account_balance(self.cash)
        result = self.complete(pid, "Credit")
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))
        self.assertEqual(result.data["paymentStatus"], "Unpaid")
        self.assertEqual(account_balance(self.cash), before)
        self.assertEqual(Expense.objects.get(pk=result.data["expenseId"]).status, "Unpaid")
        self.assertFalse(ExpenseSettlement.objects.filter(company=self.company).exists())

    def test_account_must_match_method_and_have_funds(self):
        pid = self.request_quote()
        self.approve(pid)
        mismatch = self.complete(pid, "Bank", self.cash)
        self.assertEqual(mismatch.status_code, 400)
        self.assertFalse(Expense.objects.filter(company=self.company).exists())
        self.bank.opening_balance = Decimal("1.00")
        self.bank.save(update_fields=["opening_balance"])
        insufficient = self.complete(pid, "Bank", self.bank)
        self.assertEqual(insufficient.status_code, 400)
        self.assertFalse(ExpenseSettlement.objects.filter(company=self.company).exists())
        self.assertFalse(Expense.objects.filter(company=self.company).exists())

    def test_unapproved_request_cannot_pay_or_create_expense(self):
        pid = self.request_quote()
        result = self.complete(pid, "Cash", self.cash)
        self.assertEqual(result.status_code, 400)
        self.assertFalse(Expense.objects.filter(company=self.company).exists())
        self.assertEqual(account_balance(self.cash), Decimal("800.00"))
