"""Real budget items recognize operating expenses once; payment is cash flow."""
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.expenses.models import Expense, ExpenseBudgetItem
from apps.inventory.models import WorkshopFinancialAccount, WorkshopAccountEntry
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class ExpenseBudgetIntegrationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Budget Workshop", slug="budget-workshop")
        role = Role.objects.create(
            company=self.company, name="Owner", code="BUDGET_OWNER",
            permissions=["*"], is_active=True,
        )
        self.owner = User.objects.create_user(
            email="budget-owner@example.test", name="Budget Owner",
            password="safe-password", company=self.company, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.cash = WorkshopFinancialAccount.objects.create(
            company=self.company, name="Workshop cash", kind="Cash",
            opening_balance=Decimal("5000.00"), created_by=self.owner,
        )
        self.endpoint = "/api/v1/expenses/budgets"
        self.month = "2026-11"

    def budget(self, **changes):
        data = {
            "name": "Monthly cleaning", "category": "Cleaning & Waste",
            "month": self.month, "plannedAmount": "5000.00",
            **changes,
        }
        return self.client.post(self.endpoint, data, format="json")

    def expense(self, budget_id, *, amount="2000.00", status="approved", date="2026-11-10"):
        return self.client.post("/api/v1/expenses", {
            "date": date, "category": "Cleaning & Waste",
            "description": "Workshop cleaning materials",
            "amount": amount, "tax": "0.00", "status": status,
            "budget_item": budget_id,
        }, format="json")

    def test_budget_expense_then_payment_counts_actual_once_and_debits_account(self):
        created = self.budget()
        self.assertEqual(created.status_code, 201, created.data)
        budget_id = created.data["id"]
        created_expense = self.expense(budget_id)
        self.assertEqual(created_expense.status_code, 201, created_expense.data)
        expense_id = created_expense.data["id"]
        rows = self.client.get(self.endpoint, {"month": self.month})
        self.assertEqual(rows.status_code, 200, rows.data)
        self.assertEqual(rows.data[0]["spentAmount"], "2000.00")
        self.assertEqual(rows.data[0]["remainingAmount"], "3000.00")
        self.assertFalse(WorkshopAccountEntry.objects.filter(
            company=self.company, entry_type="expense_payment",
        ).exists())
        payment = self.client.post(
            f"/api/v1/expenses/{expense_id}/settle", {
                "account": str(self.cash.id), "amount": "2000.00",
                "method": "Cash", "idempotencyKey": "cleaning-paid-once",
            }, format="json",
        )
        self.assertEqual(payment.status_code, 201, payment.data)
        retry = self.client.post(
            f"/api/v1/expenses/{expense_id}/settle", {
                "account": str(self.cash.id), "amount": "2000.00",
                "method": "Cash", "idempotencyKey": "cleaning-paid-once",
            }, format="json",
        )
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(self.client.get(
            self.endpoint, {"month": self.month},
        ).data[0]["spentAmount"], "2000.00")
        debits = WorkshopAccountEntry.objects.filter(
            company=self.company, entry_type="expense_payment",
        )
        self.assertEqual(debits.count(), 1)
        self.assertEqual(debits.first().amount, Decimal("-2000.00"))

    def test_wrong_month_category_and_foreign_budget_are_rejected(self):
        budget_id = self.budget().data["id"]
        self.assertEqual(self.expense(budget_id, date="2026-12-01").status_code, 400)
        mismatch = self.client.post("/api/v1/expenses", {
            "date": "2026-11-12", "category": "Fuel",
            "description": "Wrong category", "amount": "100",
            "budget_item": budget_id,
        }, format="json")
        self.assertEqual(mismatch.status_code, 400)
        other = Company.objects.create(name="Another Workshop", slug="another-budget-workshop")
        foreign = ExpenseBudgetItem.objects.create(
            company=other, name="Foreign", category="Cleaning & Waste",
            month="2026-11-01", planned_amount=Decimal("200.00"),
        )
        self.assertEqual(self.expense(str(foreign.pk)).status_code, 400)
        self.assertFalse(Expense.objects.filter(company=self.company).exists())

    def test_unapproved_expense_not_in_actual_and_budget_update(self):
        budget_id = self.budget().data["id"]
        self.assertEqual(self.expense(budget_id, status="Draft").status_code, 201)
        self.assertEqual(self.client.get(self.endpoint, {"month": self.month}).data[0]["spentAmount"], "0.00")
        self.assertEqual(self.expense(budget_id, status="approved", amount="6000.00").status_code, 201)
        row = self.client.get(self.endpoint, {"month": self.month}).data[0]
        self.assertEqual(row["remainingAmount"], "-1000.00")
        changed = self.client.patch(f"{self.endpoint}/{budget_id}", {
            "plannedAmount": "6500.00",
        }, format="json")
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data["remainingAmount"], "500.00")
        self.assertEqual(self.budget().status_code, 400)

    def test_staff_without_edit_cannot_create_or_change_budget(self):
        budget_id = self.budget().data["id"]
        role = Role.objects.create(
            company=self.company, name="Budget viewer", code="BUDGET_VIEWER",
            permissions=["expenses.view"], is_active=True,
        )
        viewer = User.objects.create_user(
            email="budget-viewer@example.test", company=self.company,
            name="Budget Viewer", password="safe-password", role=role,
        )
        self.client.force_authenticate(viewer)
        self.assertEqual(self.client.get(self.endpoint, {"month": self.month}).status_code, 200)
        self.assertEqual(self.budget(name="Not permitted").status_code, 403)
        self.assertEqual(self.client.patch(f"{self.endpoint}/{budget_id}", {
            "plannedAmount": "100.00",
        }, format="json").status_code, 403)

    def test_salary_and_stock_purchases_cannot_double_count_operating_expenses(self):
        for category in ("Staff Salary", "Daily Wages", "Overtime", "Inventory Purchase"):
            with self.subTest(category=category):
                result = self.client.post("/api/v1/expenses", {
                    "date": "2026-11-12", "category": category,
                    "description": category, "amount": "200.00",
                }, format="json")
                self.assertEqual(result.status_code, 400, result.data)
