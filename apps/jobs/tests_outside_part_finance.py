"""Outside-shop part payments reconcile Job Cost, Expenses and cash exactly once."""
from decimal import Decimal
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.expenses.models import Expense, ExpenseSettlement
from apps.inventory.models import WorkshopAccountEntry, WorkshopFinancialAccount, StockMovement
from apps.jobs.models import Job, OutsidePartBill
from apps.roles.models import Role
from apps.vehicles.models import Vehicle


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class OutsidePartFinanceTests(TestCase):
    def setUp(self):
        self.private_dir = TemporaryDirectory()
        self.addCleanup(self.private_dir.cleanup)
        media_override = override_settings(PRIVATE_JOB_MEDIA_ROOT=self.private_dir.name)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.company = Company.objects.create(name="Direct Part Garage", slug="direct-part-garage")
        self.branch = Branch.objects.create(
            company=self.company, name="Main", code="MAIN", is_active=True,
        )
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="DIRECT_PART_OWNER",
            permissions=["*"], is_active=True,
        )
        self.owner = User.objects.create_user(
            email="direct-part-owner@example.test", password="safe-password",
            name="Workshop Owner", company=self.company, branch=self.branch,
            role=self.role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Parts Customer", phone="9876543011",
        )
        vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=customer,
            registration="KL10AB2301", make="Honda", model="City",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch,
            customer=customer, vehicle=vehicle, job_number="PART-CASH-001",
            status="In Progress",
            workflow_progress={"completed": ["overview", "inspection", "estimate"]},
        )
        self.cash = WorkshopFinancialAccount.objects.create(
            company=self.company, branch=self.branch, name="Cash Till",
            kind="Cash", opening_balance=Decimal("1000.00"),
            created_by=self.owner,
        )
        self.bank = WorkshopFinancialAccount.objects.create(
            company=self.company, branch=self.branch, name="Business Bank",
            kind="Bank", opening_balance=Decimal("500.00"),
            created_by=self.owner,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.list_url = f"/api/v1/job-cards/{self.job.pk}/outside-parts"

    def approved_request(self, *, key="direct-part-request-1", quote="100.00", gst_rate="0"):
        request = self.client.post(self.list_url, {
            "requestId": key, "partName": "Outside brake pad",
            "vendor": "Neighbourhood Parts Store", "quantity": "2",
            "unitCost": quote, "sellingPrice": "180.00",
            "purchaseGstRate": gst_rate,
        }, format="json")
        self.assertEqual(request.status_code, 201, getattr(request, "data", None))
        part_id = request.data["id"]
        approve = self.client.post(
            f"{self.list_url}/{part_id}/decision", {"decision": "approve"}, format="json",
        )
        self.assertEqual(approve.status_code, 200, getattr(approve, "data", None))
        return part_id

    def complete(self, part_id, *, method="Cash", account_id=None, rate="95.00", paid_amount=None):
        if account_id is None and method == "Cash":
            account_id = str(self.cash.pk)
        if account_id is None and method == "Bank":
            account_id = str(self.bank.pk)
        payload = {
            "invoiceNo": "OUT-BILL-2026-1", "unitCost": rate,
            "paymentType": method,
            "bill": SimpleUploadedFile(
                "supplier-invoice.pdf", b"%PDF-1.4\n1 0 obj\n", content_type="application/pdf",
            ),
        }
        if account_id is not None:
            payload["accountId"] = account_id
        if paid_amount is not None:
            payload["paidAmount"] = paid_amount
        return self.client.post(
            f"{self.list_url}/{part_id}/complete", payload, format="multipart",
        )

    def test_cash_purchase_debits_once_and_records_expense_job_cost_and_customer_sell(self):
        part_id = self.approved_request()
        self.assertEqual(Expense.objects.count(), 0)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)
        first = self.complete(part_id)
        self.assertEqual(first.status_code, 200, getattr(first, "data", None))
        self.assertEqual(first.data["paymentType"], "Cash")
        self.assertEqual(first.data["totalCost"], "190.00")
        self.assertEqual(first.data["totalSelling"], "360.00")
        self.assertEqual(first.data["paidAmount"], "190.00")
        expense = Expense.objects.get(pk=first.data["expenseId"])
        self.assertEqual(expense.company, self.company)
        self.assertEqual(expense.status, "Paid")
        self.assertEqual(expense.amount, Decimal("190.00"))
        self.assertEqual(expense.category, "Outside Job Parts")
        self.assertEqual(expense.tax, Decimal("0.00"))
        self.assertEqual(expense.account, self.cash)
        posted = WorkshopAccountEntry.objects.filter(
            company=self.company, entry_type="expense_payment",
        )
        self.assertEqual(posted.count(), 1)
        self.assertEqual(posted.first().amount, Decimal("-190.00"))
        self.assertEqual(ExpenseSettlement.objects.count(), 1)
        self.assertEqual(OutsidePartBill.objects.count(), 1)
        self.assertEqual(StockMovement.objects.count(), 0)
        costs = self.client.get(f"/api/v1/jobs/{self.job.pk}/cost-summary")
        self.assertEqual(costs.status_code, 200, getattr(costs, "data", None))
        self.assertEqual(costs.data["outsidePartsCost"], "190.00")
        retry = self.complete(part_id)
        self.assertEqual(retry.status_code, 200, getattr(retry, "data", None))
        self.assertEqual(retry.data["expenseId"], first.data["expenseId"])
        self.assertEqual(Expense.objects.count(), 1)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 1)

    def test_bank_payment_uses_selected_bank_only(self):
        part_id = self.approved_request()
        done = self.complete(part_id, method="Bank")
        self.assertEqual(done.status_code, 200, getattr(done, "data", None))
        debit = WorkshopAccountEntry.objects.get(entry_type="expense_payment")
        self.assertEqual(debit.account, self.bank)
        self.assertEqual(debit.amount, Decimal("-190.00"))
        self.assertEqual(Expense.objects.get().payment_method, "Bank")

    def test_credit_purchase_is_expense_but_no_account_debit_until_settlement(self):
        part_id = self.approved_request()
        result = self.complete(part_id, method="Credit")
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))
        self.assertEqual(result.data["paidAmount"], "0.00")
        expense = Expense.objects.get(pk=result.data["expenseId"])
        self.assertEqual(expense.status, "Unpaid")
        self.assertEqual(expense.amount, Decimal("190.00"))
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)
        payment = self.client.post(
            f"/api/v1/expenses/{expense.pk}/settle", {
                "account": str(self.bank.pk), "amount": "190.00",
                "method": "Bank Transfer", "idempotencyKey": "pay-later-part-001",
            }, format="json",
        )
        self.assertEqual(payment.status_code, 201, getattr(payment, "data", None))
        self.assertEqual(WorkshopAccountEntry.objects.count(), 1)
        self.assertEqual(self.client.get(self.list_url).data["items"][0]["status"], "Purchased")

    def test_partial_bank_purchase_posts_exact_cash_only_and_leaves_credit(self):
        part_id = self.approved_request(gst_rate="5")
        result = self.complete(part_id, method="Bank", rate="95.00", paid_amount="100.00")
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))
        self.assertEqual(result.data["purchaseBase"], "190.00")
        self.assertEqual(result.data["purchaseGstAmount"], "9.50")
        self.assertEqual(result.data["totalCost"], "199.50")
        self.assertEqual(result.data["paidAmount"], "100.00")
        self.assertEqual(result.data["balanceDue"], "99.50")
        self.assertEqual(result.data["paymentStatus"], "Partially Paid")
        expense = Expense.objects.get(pk=result.data["expenseId"])
        self.assertEqual(expense.amount, Decimal("199.50"))
        self.assertEqual(expense.tax, Decimal("0.00"))  # No unverified input tax credit.
        self.assertEqual(expense.status, "Partially Paid")
        self.assertEqual(ExpenseSettlement.objects.count(), 1)
        ledger = WorkshopAccountEntry.objects.get(entry_type="expense_payment")
        self.assertEqual(ledger.account_id, self.bank.pk)
        self.assertEqual(ledger.amount, Decimal("-100.00"))
        self.assertEqual(StockMovement.objects.count(), 0)
        payment = self.client.post(
            f"/api/v1/expenses/{expense.pk}/settle",
            {"account": str(self.cash.pk), "amount": "99.50",
             "method": "Cash", "idempotencyKey": "part-credit-settlement-2026"},
            format="json",
        )
        self.assertEqual(payment.status_code, 201, getattr(payment, "data", None))
        expense.refresh_from_db()
        self.assertEqual(expense.status, "Paid")
        self.assertEqual(ExpenseSettlement.objects.count(), 2)
        self.assertEqual(
            sum((entry.amount for entry in WorkshopAccountEntry.objects.all()), Decimal("0")),
            Decimal("-199.50"),
        )
        retried = self.complete(part_id, method="Bank", paid_amount="100.00")
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 2)
        self.assertEqual(Expense.objects.count(), 1)

    def test_invalid_partial_payment_rejects_without_cash_stock_or_expense(self):
        for index, amount in enumerate(("200.00", "-1.00", "0.00", "1.001", "NaN", "abc")):
            with self.subTest(amount=amount):
                part_id = self.approved_request(key=f"invalid-part-payment-{index}")
                response = self.complete(part_id, paid_amount=amount)
                self.assertEqual(response.status_code, 400, getattr(response, "data", None))
        self.assertEqual(Expense.objects.count(), 0)
        self.assertEqual(OutsidePartBill.objects.count(), 0)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_credit_cannot_send_nonzero_paid_amount(self):
        part_id = self.approved_request()
        failed = self.complete(part_id, method="Credit", paid_amount="20.00")
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(Expense.objects.count(), 0)

    def test_invalid_account_method_and_insufficient_balance_fail_atomically(self):
        part_id = self.approved_request()
        self.assertEqual(
            self.complete(part_id, method="Cash", account_id=str(self.bank.pk)).status_code,
            400,
        )
        other = Company.objects.create(name="Foreign Garage", slug="foreign-direct-garage")
        foreign_account = WorkshopFinancialAccount.objects.create(
            company=other, name="Foreign Cash", kind="Cash",
            opening_balance=Decimal("9999.00"),
        )
        self.assertEqual(
            self.complete(part_id, method="Cash", account_id=str(foreign_account.pk)).status_code,
            404,
        )
        self.cash.opening_balance = Decimal("5.00")
        self.cash.save(update_fields=["opening_balance"])
        self.assertEqual(self.complete(part_id).status_code, 400)
        self.assertEqual(Expense.objects.count(), 0)
        self.assertEqual(WorkshopAccountEntry.objects.count(), 0)
        self.assertEqual(OutsidePartBill.objects.count(), 0)
        self.job.refresh_from_db()
        self.assertEqual(self.job.parts_workflow["outsideParts"][0]["status"], "Approved")

    def test_requires_edit_expense_permission_to_record_paid_purchase(self):
        part_id = self.approved_request()
        role = Role.objects.create(
            company=self.company, name="Jobs viewer", code="PART_FINANCE_READ",
            permissions=["jobs.view", "jobs.edit", "expenses.view"], is_active=True,
        )
        reader = User.objects.create_user(
            company=self.company, branch=self.branch, role=role,
            name="Reader", email="part-finance-reader@example.test",
            password="safe-password",
        )
        self.client.force_authenticate(reader)
        response = self.complete(part_id)
        self.assertEqual(response.status_code, 403, getattr(response, "data", None))
        self.assertFalse(Expense.objects.exists())
