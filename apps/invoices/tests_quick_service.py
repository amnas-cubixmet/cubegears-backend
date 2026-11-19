from decimal import Decimal
from uuid import uuid4

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.inventory.models import WorkshopAccountEntry, WorkshopFinancialAccount, StockItem, StockBranchBalance
from apps.services.models import Service
from apps.jobs.models import OutsideLabourCharge
from apps.expenses.models import Expense
from apps.payments.models import Payment
from apps.roles.models import Role
from apps.vehicles.models import Vehicle
from .models import Invoice, QuickService


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False, CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class QuickServiceTests(TestCase):
    url = "/api/v1/invoices/quick-services"

    def setUp(self):
        self.company = Company.objects.create(name="Quick Workshop", slug="quick-workshop", state="Kerala")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN", state="Kerala")
        self.role = Role.objects.create(company=self.company, name="Workshop", code="QUICK", permissions=["*"])
        self.user = User.objects.create_user(
            email="quick@example.test", password="test-password", name="Counter",
            company=self.company, branch=self.branch, role=self.role,
        )
        self.company.owner = self.user
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.cash = WorkshopFinancialAccount.objects.create(company=self.company, branch=self.branch, name="Cash drawer", kind="Cash")
        self.bank = WorkshopFinancialAccount.objects.create(company=self.company, branch=self.branch, name="UPI account", kind="UPI")
        self.items = [{"description": "Tyre puncture repair", "qty": "2", "rate": "150.00"}]

    def start(self):
        payload = {"description": "Tyre puncture repair", "requestId": str(uuid4())}
        result = self.client.post(self.url, payload, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 200)
        self.assertIsNone(result.data["invoiceId"])
        self.assertFalse(Invoice.objects.exists())
        return result.data["id"]

    def checkout(self, service, *, method="Cash", account=None, total="300.00"):
        return self.client.post(f"{self.url}/{service}/close", {
            "items": self.items, "paymentMethod": method, "account": str((account or self.cash).pk),
            "expectedTotal": total,
        }, format="json")

    def test_cash_closes_and_posts_once_without_customer_vehicle_or_job(self):
        service = self.start()
        quote = self.client.post(f"{self.url}/{service}/preview", {"items": self.items}, format="json")
        self.assertEqual(quote.status_code, 200, quote.data)
        self.assertEqual(quote.data["total"], "300.00")
        self.assertFalse(Invoice.objects.exists())
        self.assertEqual(self.checkout(service, total="299.99").status_code, 400)
        result = self.checkout(service)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["invoiceStatus"], "Paid")
        invoice = Invoice.objects.get()
        self.assertEqual(result.data["invoiceId"], str(invoice.pk))
        self.assertEqual(invoice.quick_service_id, QuickService.objects.get().pk)
        self.assertEqual(invoice.paid, Decimal("300.00"))
        self.assertEqual(invoice.balance, 0)
        self.assertIsNone(invoice.customer_id)
        self.assertIsNone(invoice.vehicle_id)
        self.assertIsNone(invoice.job_id)
        self.assertFalse(Customer.objects.exists())
        self.assertFalse(Vehicle.objects.exists())
        self.assertEqual(self.checkout(service).data, result.data)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(WorkshopAccountEntry.objects.get(account=self.cash).amount, Decimal("300.00"))
        detail = self.client.get(f"/api/v1/invoices/{invoice.pk}")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["customer"], {})
        pdf = self.client.get(f"/api/v1/invoices/{invoice.pk}/pdf", HTTP_ACCEPT="application/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_checkout_rolls_back_on_invalid_channel_and_bank_retry_works(self):
        service = self.start()
        failed = self.checkout(service, method="Cash", account=self.bank)
        self.assertEqual(failed.status_code, 400, failed.data)
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(WorkshopAccountEntry.objects.exists())
        result = self.checkout(service, method="Bank", account=self.bank)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(Payment.objects.get().account, self.bank)

    def test_unregistered_customer_gst_invoice_uses_branch_tax_and_checks_recipient_limit(self):
        self.company.gstin = "32AAIFF2342J1ZO"
        self.company.save(update_fields=["gstin"])
        service = self.start()
        self.assertEqual(self.checkout(service).status_code, 400)
        self.items[0].update(hsnCode="998729", taxRate="18.00", rate="25000.00")
        self.assertEqual(self.checkout(service, total="59000.00").status_code, 400)
        self.items[0]["rate"] = "150.00"
        result = self.checkout(service, total="354.00")
        self.assertEqual(result.status_code, 200, result.data)
        invoice = Invoice.objects.get()
        self.assertEqual((invoice.cgst, invoice.sgst, invoice.igst), (Decimal("27.00"), Decimal("27.00"), 0))
        self.assertEqual(invoice.customer_snapshot, {"placeOfSupply": "32"})


    def test_full_quick_service_form_links_customer_vehicle_catalogue_parts_and_outside_labour(self):
        self.company.gstin = "32AAIFF2342J1ZO"
        self.company.save(update_fields=["gstin"])
        customer = Customer.objects.create(
            company=self.company, branch=self.branch, name="Anil Kumar",
            phone="+919000000111", state="Kerala",
        )
        vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=customer,
            registration="KL10AB1234", make="Maruti", model="Swift",
        )
        service = Service.objects.create(
            company=self.company, name="Quick Check", code="QCK-01",
            price=Decimal("1000.00"), tax_rate=Decimal("18.00"),
            hsn_sac="998729", unit="Job",
        )
        part = StockItem.objects.create(
            company=self.company, branch=self.branch, name="Oil Filter",
            sku="FLT-001", selling_price=Decimal("250.00"),
            cost_price=Decimal("150.00"), on_hand=Decimal("5.00"),
            tax=Decimal("18.00"), hsn_code="84212300",
        )
        payload = {
            "requestId": str(uuid4()),
            "description": "Quick oil service",
            "customerId": str(customer.pk),
            "vehicleId": str(vehicle.pk),
            "items": [
                {"kind": "service", "serviceId": str(service.pk), "qty": "1"},
                {"kind": "part", "inventoryId": str(part.pk), "qty": "2"},
                {
                    "kind": "outside", "workerName": "Ravi Painter",
                    "workerPhone": "+919000000222", "workDescription": "Small touch-up",
                    "customerCharge": "500.00", "workerCharge": "300.00",
                    "hsnCode": "998729", "taxRate": "18.00",
                },
            ],
            "notes": "Counter service",
        }
        created = self.client.post(self.url, payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["customerId"], str(customer.pk))
        self.assertEqual(created.data["vehicleId"], str(vehicle.pk))
        self.assertEqual(len(created.data["items"]), 3)
        quick_id = created.data["id"]
        outside = OutsideLabourCharge.objects.get(quick_service_id=quick_id)
        self.assertIsNone(outside.job_id)
        self.assertEqual(outside.status, OutsideLabourCharge.STATUS_PENDING)

        quote = self.client.post(f"{self.url}/{quick_id}/preview", {}, format="json")
        self.assertEqual(quote.status_code, 200, quote.data)
        self.assertEqual(quote.data["total"], "2360.00")
        closed = self.client.post(f"{self.url}/{quick_id}/close", {
            "paymentMethod": "Cash", "account": str(self.cash.pk),
            "expectedTotal": "2360.00",
        }, format="json")
        self.assertEqual(closed.status_code, 200, closed.data)
        invoice = Invoice.objects.get(quick_service_id=quick_id)
        self.assertEqual(invoice.customer_id, customer.pk)
        self.assertEqual(invoice.vehicle_id, vehicle.pk)
        self.assertIsNone(invoice.job_id)
        self.assertEqual(invoice.items.count(), 3)
        self.assertEqual(invoice.items.get(stock_item=part).quantity, Decimal("2.00"))
        part.refresh_from_db()
        self.assertEqual(part.on_hand, Decimal("3.00"))
        branch_balance = StockBranchBalance.objects.get(item=part, branch=self.branch)
        self.assertEqual(branch_balance.quantity, Decimal("3.00"))
        self.assertEqual(outside.customer_charge, Decimal("500.00"))
        self.assertEqual(outside.worker_charge, Decimal("300.00"))

        replay = self.client.post(self.url, payload, format="json")
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(QuickService.objects.count(), 1)
        self.assertEqual(OutsideLabourCharge.objects.filter(quick_service_id=quick_id).count(), 1)
        self.assertEqual(Payment.objects.count(), 1)

        paid = self.client.post(
            f"/api/v1/outside-labour/{outside.pk}/pay",
            {"paymentMethod": "Cash"}, format="json",
        )
        self.assertEqual(paid.status_code, 200, paid.data)
        outside.refresh_from_db()
        self.assertEqual(outside.status, OutsideLabourCharge.STATUS_PAID)
        self.assertTrue(Expense.objects.filter(pk=outside.expense_id).exists())

    def test_registered_invoice_path_still_requires_a_customer(self):
        result = self.client.post("/api/v1/invoices", {
            "date": "2026-11-19", "items": self.items, "quickServiceId": str(uuid4()),
        }, format="json")
        self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(Invoice.objects.exists())

    def test_checkout_requires_billing_permissions_and_scopes_service_and_account(self):
        service = self.start()
        limited = Role.objects.create(company=self.company, name="Mechanic", code="MECH", permissions=["jobs.view", "jobs.edit"])
        worker = User.objects.create_user(email="mechanic@example.test", name="Mechanic", company=self.company, role=limited)
        self.client.force_authenticate(user=worker)
        self.assertEqual(self.checkout(service).status_code, 403)

        other = Company.objects.create(name="Another Workshop", slug="another-workshop")
        other_role = Role.objects.create(company=other, name="Owner", code="OTHER", permissions=["*"])
        other_user = User.objects.create_user(email="other@example.test", name="Owner", company=other, role=other_role)
        other.owner = other_user
        other.save(update_fields=["owner"])
        foreign = WorkshopFinancialAccount.objects.create(company=other, name="Foreign cash", kind="Cash")
        self.client.force_authenticate(user=other_user)
        self.assertEqual(self.checkout(service, account=foreign).status_code, 404)
        self.client.force_authenticate(user=self.user)
        self.assertEqual(self.checkout(service, account=foreign).status_code, 400)
        self.assertFalse(Invoice.objects.exists())

        self.assertEqual(self.checkout(service).status_code, 200)
        self.client.force_authenticate(user=worker)
        result = self.client.get(f"{self.url}/{service}")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data["closedAt"])
        self.assertNotIn("invoiceId", result.data)
        self.assertNotIn("invoiceTotal", result.data)
