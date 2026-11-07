"""Part 12 real REST checks for branch stock, physical counts and returns."""
from decimal import Decimal
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.inventory.models import (
    StockAudit, StockBranchBalance, StockItem, StockMovement, StockTransfer,
)
from apps.roles.models import Role


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False, CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class InventoryPart12Tests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Stock Ledger Garage", slug="stock-ledger-garage")
        self.a = Branch.objects.create(company=self.company, name="Central", code="CTR", is_active=True)
        self.b = Branch.objects.create(company=self.company, name="East", code="EST", is_active=True)
        owner_role = Role.objects.create(
            company=self.company, name="Owner", code="PART12_STOCK_OWNER",
            permissions=["*"], is_system=True, is_active=True,
        )
        self.owner = User.objects.create_user(
            email="part12@example.test", name="Stock Owner", password="StrongPassword123",
            company=self.company, branch=self.a, role=owner_role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.part = StockItem.objects.create(
            company=self.company, branch=self.a, name="Brake Oil",
            sku="PART12-BRAKE-01", cost_price="100.00", selling_price="150.00",
            on_hand=Decimal("5.00"),
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def transfer(self, amount="2.00", key="part12-transfer-once", target=None):
        return self.client.post("/api/v1/stock/transfer", {
            "fromBranch": str(self.a.pk), "toBranch": str(target or self.b.pk),
            "itemId": str(self.part.pk), "quantity": amount, "requestKey": key,
        }, format="json")

    def balances(self):
        return dict(StockBranchBalance.objects.filter(
            company=self.company, item=self.part,
        ).values_list("branch_id", "quantity"))

    def test_transfer_posts_paired_movements_and_keeps_company_quantity(self):
        first = self.transfer()
        self.assertEqual(first.status_code, 201, getattr(first, "data", None))
        self.part.refresh_from_db()
        self.assertEqual(self.part.on_hand, Decimal("5.00"))
        self.assertEqual(self.balances()[self.a.pk], Decimal("3.00"))
        self.assertEqual(self.balances()[self.b.pk], Decimal("2.00"))
        self.assertEqual(StockMovement.objects.filter(reference=f"TRANSFER:{first.data['id']}").count(), 2)
        again = self.transfer()
        self.assertEqual(again.status_code, 200, getattr(again, "data", None))
        self.assertEqual(StockTransfer.objects.count(), 1)
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assertEqual(self.transfer("2.01").status_code, 400)
        self.assertEqual(self.transfer("4.00", key="part12-not-enough").status_code, 400)
        self.assertEqual(self.transfer(target=self.a.pk, key="part12-same-branch").status_code, 400)

    def test_issue_from_receiving_branch_and_return_issued_units_only(self):
        self.assertEqual(self.transfer().status_code, 201)
        self.owner.branch = self.b
        self.owner.save(update_fields=["branch"])
        self.client.force_authenticate(self.owner)
        issue = self.client.post("/api/v1/stock/issue", {
            "itemId": str(self.part.pk), "quantity": "1.00", "requestKey": "part12-issue-once",
        }, format="json")
        self.assertEqual(issue.status_code, 201, getattr(issue, "data", None))
        self.assertEqual(self.client.post("/api/v1/stock/issue", {
            "itemId": str(self.part.pk), "quantity": "1.00", "requestKey": "part12-issue-once",
        }, format="json").status_code, 200)
        self.assertEqual(self.balances()[self.b.pk], Decimal("1.00"))
        returned = self.client.post("/api/v1/stock/return", {
            "issueMovementId": issue.data["id"], "itemId": str(self.part.pk),
            "quantity": "1.00", "requestKey": "part12-return-once",
            "condition": "Reusable",
        }, format="json")
        self.assertEqual(returned.status_code, 201, getattr(returned, "data", None))
        self.assertEqual(self.client.post("/api/v1/stock/return", {
            "issueMovementId": issue.data["id"], "itemId": str(self.part.pk),
            "quantity": "1.00", "requestKey": "part12-return-once",
        }, format="json").status_code, 200)
        self.assertEqual(self.client.post("/api/v1/stock/return", {
            "issueMovementId": issue.data["id"], "quantity": "1.00",
            "requestKey": "part12-return-too-much",
        }, format="json").status_code, 400)
        self.assertEqual(self.balances()[self.b.pk], Decimal("2.00"))
        self.part.refresh_from_db()
        self.assertEqual(self.part.on_hand, Decimal("5.00"))

    def test_count_posts_real_variance_and_safe_retry(self):
        payload = {
            "itemId": str(self.part.pk), "countedQty": "3.00",
            "reason": "Physical shelf count", "requestKey": "part12-count-once",
        }
        result = self.client.post("/api/v1/stock/audit", payload, format="json")
        self.assertEqual(result.status_code, 201, getattr(result, "data", None))
        self.assertEqual(self.client.post("/api/v1/stock/audit", payload, format="json").status_code, 200)
        self.assertEqual(StockAudit.objects.count(), 1)
        self.assertEqual(self.balances()[self.a.pk], Decimal("3.00"))
        self.part.refresh_from_db()
        self.assertEqual(self.part.on_hand, Decimal("3.00"))
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(self.client.patch(f"/api/v1/stock/audit/{result.data['id']}", {
            "counted_qty": "100",
        }, format="json").status_code, 405)
        zero = self.client.post("/api/v1/stock/audit", {
            **payload, "countedQty": "0.00", "requestKey": "part12-count-zero",
        }, format="json")
        self.assertEqual(zero.status_code, 201, getattr(zero, "data", None))
        self.part.refresh_from_db()
        self.assertEqual(self.part.on_hand, Decimal("0.00"))

    def test_direct_issue_and_receipt_are_idempotent(self):
        issue = {
            "itemId": str(self.part.pk), "quantity": "2.00",
            "requestKey": "part12-direct-issue",
        }
        self.assertEqual(self.client.post("/api/v1/stock/issue", issue, format="json").status_code, 201)
        self.assertEqual(self.client.post("/api/v1/stock/issue", issue, format="json").status_code, 200)
        self.part.refresh_from_db()
        self.assertEqual(self.part.on_hand, Decimal("3.00"))
        purchase = {
            "itemId": str(self.part.pk), "quantity": "1.00",
            "costPrice": "100.00", "sellingPrice": "150.00",
            "requestKey": "part12-direct-receipt",
        }
        self.assertEqual(self.client.post("/api/v1/stock/in", purchase, format="json").status_code, 201)
        self.assertEqual(self.client.post("/api/v1/stock/in", purchase, format="json").status_code, 200)
        self.part.refresh_from_db()
        self.assertEqual(self.part.on_hand, Decimal("4.00"))
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assertEqual(self.client.post("/api/v1/stock/in", {
            **purchase, "costPrice": "105.00",
        }, format="json").status_code, 400)

    def test_cannot_mutate_transfer_ledger_or_cross_workshop(self):
        other = Company.objects.create(name="Foreign", slug="part12-foreign")
        foreign = Branch.objects.create(company=other, name="Foreign", code="F", is_active=True)
        self.assertEqual(self.transfer(target=foreign.pk, key="part12-cross-tenant").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/stock/transfers", {
            "from_branch": str(self.a.pk), "to_branch": str(self.b.pk),
            "number": "FORGED", "status": "Completed",
        }, format="json").status_code, 405)
        self.assertEqual(self.client.post("/api/v1/stock/movements", {
            "item": str(self.part.pk), "quantity": "400.00",
            "movement_type": "purchase",
        }, format="json").status_code, 405)
        self.assertEqual(StockMovement.objects.count(), 0)
