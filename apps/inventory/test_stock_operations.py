from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.inventory.models import StockItem, StockMovement
from apps.roles.models import Role


class StockSafetyTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Easy Stock Garage", slug="easy-stock-garage")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        self.role = Role.objects.create(
            company=self.company, name="Stock Manager", code="STOCK",
            permissions=["stock.view", "stock.create", "stock.edit", "stock.delete"],
        )
        self.user = User.objects.create(
            company=self.company, branch=self.branch, role=self.role,
            name="Stock Manager", email="stock-manager@example.test",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.item = StockItem.objects.create(
            company=self.company, branch=self.branch,
            name="Brake Pad", sku="BRAKE-001",
            on_hand=Decimal("5.00"), reserved=Decimal("1.00"),
            cost_price=Decimal("125.00"),
        )

    def test_receive_and_issue_are_logged_and_balanced(self):
        receive = self.client.post("/api/v1/stock/in", {
            "itemId": str(self.item.id), "quantity": "3.50", "invoiceNo": "INV-11",
        }, format="json")
        self.assertEqual(receive.status_code, 201, receive.data)
        issue = self.client.post("/api/v1/stock/issue", {
            "itemId": str(self.item.id), "quantity": "2.00", "notes": "Cleaning supplies",
        }, format="json")
        self.assertEqual(issue.status_code, 201, issue.data)
        self.item.refresh_from_db()
        self.assertEqual(self.item.on_hand, Decimal("6.50"))
        movements = list(StockMovement.objects.filter(item=self.item).order_by("created_at"))
        self.assertEqual([x.movement_type for x in movements], ["purchase", "sale"])
        self.assertEqual([x.quantity for x in movements], [Decimal("3.50"), Decimal("-2.00")])

    def test_cannot_issue_reserved_or_unavailable_stock(self):
        result = self.client.post("/api/v1/stock/issue", {
            "itemId": str(self.item.id), "quantity": "4.01",
        }, format="json")
        self.assertEqual(result.status_code, 400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.on_hand, Decimal("5.00"))
        self.assertFalse(StockMovement.objects.filter(item=self.item).exists())

    def test_quantity_validation_never_changes_balance(self):
        for value in ["-1", "0", "abc", "NaN", "0.001"]:
            response = self.client.post("/api/v1/stock/in", {
                "itemId": str(self.item.id), "quantity": value,
            }, format="json")
            self.assertEqual(response.status_code, 400, value)
        self.item.refresh_from_db()
        self.assertEqual(self.item.on_hand, Decimal("5.00"))

    def test_stock_is_isolated_by_company_and_branch(self):
        other_company = Company.objects.create(name="Other Garage", slug="other-garage")
        other_item = StockItem.objects.create(
            company=other_company, name="Other", sku="OTHER-001", on_hand=10,
        )
        company_result = self.client.post("/api/v1/stock/in", {
            "itemId": str(other_item.id), "quantity": 1,
        }, format="json")
        self.assertEqual(company_result.status_code, 404)

        other_branch = Branch.objects.create(company=self.company, name="Secondary", code="SEC")
        other_branch_item = StockItem.objects.create(
            company=self.company, branch=other_branch, name="Second Branch", sku="SEC-001", on_hand=10,
        )
        branch_result = self.client.post("/api/v1/stock/issue", {
            "itemId": str(other_branch_item.id), "quantity": 1,
        }, format="json")
        self.assertEqual(branch_result.status_code, 403)

    def test_initial_stock_creates_opening_ledger_entry(self):
        created = self.client.post("/api/v1/stock", {
            "partName": "Air Filter", "sku": "AIR-001", "onHand": "4.00",
        }, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        saved = StockItem.objects.get(company=self.company, sku="AIR-001")
        opening = StockMovement.objects.get(item=saved, movement_type="opening")
        self.assertEqual(opening.quantity, Decimal("4.00"))

    def test_direct_stock_balance_edit_is_rejected(self):
        change = self.client.patch("/api/v1/stock/" + str(self.item.id), {
            "onHand": "99.00",
        }, format="json")
        self.assertEqual(change.status_code, 400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.on_hand, Decimal("5.00"))

    def test_manual_ledger_creation_cannot_bypass_inventory_validation(self):
        response = self.client.post("/api/v1/stock/movements", {
            "item": str(self.item.id),
            "movement_type": "purchase",
            "quantity": "100",
        }, format="json")
        self.assertEqual(response.status_code, 405)
        self.item.refresh_from_db()
        self.assertEqual(self.item.on_hand, Decimal("5.00"))
