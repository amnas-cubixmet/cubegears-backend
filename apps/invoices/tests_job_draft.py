"""Job invoice draft is read-only and never bills costs or re-issues stock."""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.inventory.models import StockItem, StockMovement
from apps.invoices.models import Invoice
from apps.jobs.models import Job, JobPart, OutsideLabourCharge
from apps.roles.models import Role
from apps.vehicles.models import Vehicle


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
                   CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class JobInvoiceDraftTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Draft Workshop", slug="draft-workshop")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        role = Role.objects.create(company=self.company, name="Owner", code="DRAFT_OWNER",
                                   permissions=["*"], is_active=True)
        self.owner = User.objects.create_user(
            email="draft-owner@example.test", password="pass-for-tests",
            name="Owner", company=self.company, branch=self.branch, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Buyer", phone="+919100000011",
        )
        vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch,
            customer=customer, registration="KL10AA4040", make="Suzuki", model="Swift",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch,
            customer=customer, vehicle=vehicle, job_number="DRAFT-JOB-001",
            status="Ready for Delivery",
            workflow_progress={"completed": ["overview", "inspection", "estimate", "work", "qc"]},
            parts_workflow={"outsideParts": [
                {"id": "Q1", "status": "Pending Approval", "partName": "Unapproved", "quantity": "2",
                 "unitCost": "300", "sellingPrice": "800"},
                {"id": "P1", "status": "Purchased", "partName": "Special paint", "quantity": "2",
                 "unitCost": "300", "sellingPrice": "800", "totalCost": "600"},
                {"id": "P2", "status": "Purchased", "partName": "Nonbillable", "quantity": "1",
                 "unitCost": "700", "sellingPrice": "0"},
                {"id": "P3", "status": "Purchased", "partName": "Not billed", "quantity": "1",
                 "unitCost": "100", "sellingPrice": "900", "billable": False},
            ]},
        )
        self.stock = StockItem.objects.create(
            company=self.company, branch=self.branch,
            name="Brake Pad", sku="PAD-01", selling_price="500", cost_price="250",
            on_hand="9", tax="18", hsn_code="8708",
        )
        JobPart.objects.create(
            company=self.company, branch=self.branch,
            job=self.job, item=self.stock, quantity="2", unit_price="450", issued=True,
        )
        OutsideLabourCharge.objects.create(
            company=self.company, branch=self.branch, job=self.job,
            worker_name="Contractor", work_description="Panel painting",
            worker_charge="200", customer_charge="650",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.url = "/api/v1/billing/documents/job-draft?jobId=" + str(self.job.pk)

    def test_only_recorded_billable_revenue_is_suggested_and_endpoint_never_posts(self):
        before = (Invoice.objects.count(), StockMovement.objects.count(), Decimal(str(self.stock.on_hand)))
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200, getattr(res, "data", None))
        self.assertFalse(res.data["created"])
        self.assertEqual(res.data["jobNumber"], "DRAFT-JOB-001")
        rows = res.data["items"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(set(row["description"] for row in rows),
                         {"Brake Pad", "Panel painting", "Special paint"})
        part = next(row for row in rows if row["description"] == "Brake Pad")
        self.assertEqual(Decimal(part["qty"]), Decimal("2"))
        self.assertEqual(part["rate"], "450.00")
        self.assertEqual(part["inventoryId"], "")
        self.assertEqual(part["type"], "Custom Item")
        self.assertEqual(part["code"], "PAD-01")
        paint = next(row for row in rows if row["description"] == "Special paint")
        self.assertEqual(paint["rate"], "800.00")
        self.assertEqual(paint["inventoryId"], "")
        self.assertFalse(any(row["rate"] in ("250.00", "300.00", "200.00", "700.00")
                             for row in rows))
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(
            (Invoice.objects.count(), StockMovement.objects.count(), self.stock.on_hand), before
        )

    def test_quality_check_required(self):
        self.job.status = "In Progress"
        self.job.workflow_progress["completed"] = ["overview", "inspection", "estimate"]
        self.job.save(update_fields=["status", "workflow_progress"])
        self.assertEqual(self.client.get(self.url).status_code, 400)

    def test_existing_invoice_is_returned_and_never_duplicated(self):
        invoice = Invoice.objects.create(
            company=self.company, branch=self.branch,
            customer=self.job.customer, vehicle=self.job.vehicle,
            job=self.job, kind="invoice", date=date.today(),
            number="INV-EXISTING", status="Draft",
        )
        reply = self.client.get(self.url)
        self.assertEqual(reply.status_code, 200, reply.data)
        self.assertEqual(reply.data["existingInvoiceId"], str(invoice.pk))
        self.assertEqual(reply.data["items"], [])
        self.assertEqual(Invoice.objects.count(), 1)

    def test_view_only_and_cross_company_users_are_denied(self):
        read_role = Role.objects.create(company=self.company, name="Reader",
                                        code="DRAFT_READER", permissions=["invoices.view"],
                                        is_active=True)
        viewer = User.objects.create_user(
            email="draft-viewer@example.test", password="test-pass",
            name="Viewer", company=self.company, role=read_role,
        )
        self.client.force_authenticate(user=viewer)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        foreign = Company.objects.create(name="Foreign", slug="foreign-draft-workshop")
        role = Role.objects.create(company=foreign, name="Owner", code="FOREIGN_DRAFT_OWNER",
                                   permissions=["*"], is_active=True)
        other = User.objects.create_user(
            email="draft-foreign@example.test", password="test-pass",
            name="Other", company=foreign, role=role,
        )
        self.client.force_authenticate(user=other)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def _invoice_payload(self):
        return {
            "kind": "invoice", "date": date.today().isoformat(),
            "invoiceType": "regular",
            "jobCardNo": self.job.job_number,
            "sourceJobId": str(self.job.pk),
            "customer": {"name": self.job.customer.name, "phone": self.job.customer.phone},
            "vehicle": {"registration": self.job.vehicle.registration},
            "items": [{"type": "Labour", "description": "Verified work",
                       "qty": "1", "rate": "100", "unit": "JOB", "taxRate": "0"}],
        }

    def test_direct_invoice_create_requires_qc(self):
        self.job.status = "In Progress"
        self.job.workflow_progress["completed"] = ["overview", "inspection", "estimate"]
        self.job.save(update_fields=["status", "workflow_progress"])
        res = self.client.post("/api/v1/billing/documents", self._invoice_payload(), format="json")
        self.assertEqual(res.status_code, 400, getattr(res, "data", None))
        self.assertEqual(Invoice.objects.count(), 0)

    def test_direct_create_rejects_second_active_job_invoice(self):
        Invoice.objects.create(
            company=self.company, branch=self.branch,
            customer=self.job.customer, vehicle=self.job.vehicle, job=self.job,
            kind="invoice", date=date.today(), number="INV-ALREADY", status="Draft",
        )
        res = self.client.post("/api/v1/billing/documents", self._invoice_payload(), format="json")
        self.assertEqual(res.status_code, 400, getattr(res, "data", None))
        self.assertEqual(Invoice.objects.count(), 1)

    def test_mismatched_source_and_job_number_are_rejected(self):
        payload = self._invoice_payload()
        payload["jobCardNo"] = "ANOTHER-JOB"
        res = self.client.post("/api/v1/billing/documents", payload, format="json")
        self.assertEqual(res.status_code, 400, getattr(res, "data", None))
        self.assertEqual(Invoice.objects.count(), 0)

    def test_job_estimate_cannot_be_converted_to_actual_invoice(self):
        estimate = Invoice.objects.create(
            company=self.company, branch=self.branch,
            customer=self.job.customer, vehicle=self.job.vehicle, job=self.job,
            kind="estimate", date=date.today(), number="EST-JOB", status="Draft",
        )
        res = self.client.post(
            f"/api/v1/billing/documents/{estimate.pk}/convert-to-invoice", {}, format="json",
        )
        self.assertEqual(res.status_code, 400, getattr(res, "data", None))
        self.assertFalse(Invoice.objects.filter(job=self.job, kind="invoice").exists())

    def test_zero_percent_recorded_tax_is_not_dropped_and_zero_work_qty_is_not_billed(self):
        self.company.gstin = "32AAAAA0000A1Z5"
        self.company.save(update_fields=["gstin"])
        self.stock.tax = Decimal("0")
        self.stock.save(update_fields=["tax"])
        self.job.work = [{"id": "invalid-zero", "service": "Not completed",
                          "billable": True, "quantity": 0, "customerRate": "500"}]
        self.job.save(update_fields=["work"])
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200, getattr(res, "data", None))
        part = next(row for row in res.data["items"] if row["description"] == "Brake Pad")
        self.assertEqual(Decimal(part["taxRate"]), Decimal("0"))
        self.assertNotIn("Not completed", [row["description"] for row in res.data["items"]])

    def test_delivery_records_handover_and_cannot_repeat(self):
        invoice = Invoice.objects.create(
            company=self.company, branch=self.branch,
            customer=self.job.customer, vehicle=self.job.vehicle, job=self.job,
            kind="invoice", date=date.today(), number="INV-DELIVERY", status="Finalized",
        )
        self.assertIsNotNone(invoice.pk)
        url = f"/api/v1/jobs/{self.job.pk}/workflow"
        missing = self.client.post(url, {"stage": "invoice"}, format="json")
        self.assertEqual(missing.status_code, 400, getattr(missing, "data", None))
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "Ready for Delivery")

        invalid = self.client.post(url, {
            "stage": "invoice", "delivery": {
                "finalKm": -1, "acknowledgedBy": "Buyer",
            },
        }, format="json")
        self.assertEqual(invalid.status_code, 400, getattr(invalid, "data", None))

        reply = self.client.post(url, {
            "stage": "invoice", "delivery": {
                "finalKm": "1200", "acknowledgedBy": "Buyer",
                "warrantyNotes": "Check after 30 days",
            },
        }, format="json")
        self.assertEqual(reply.status_code, 200, getattr(reply, "data", None))
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "Delivered")
        self.assertIsNotNone(self.job.delivered_at)
        self.assertEqual(self.job.workflow_progress["delivery"]["finalKm"], 1200)
        self.assertEqual(
            self.job.workflow_progress["delivery"]["acknowledgedBy"], "Buyer",
        )
        job_detail = self.client.get(f"/api/v1/jobs/{self.job.pk}")
        self.assertEqual(job_detail.status_code, 200, getattr(job_detail, "data", None))
        self.assertEqual(job_detail.data["delivery"]["warrantyNotes"], "Check after 30 days")
        repeated = self.client.post(url, {
            "stage": "invoice", "delivery": {
                "finalKm": "1400", "acknowledgedBy": "Another person",
            },
        }, format="json")
        self.assertEqual(repeated.status_code, 400, getattr(repeated, "data", None))
        self.job.refresh_from_db()
        self.assertEqual(self.job.workflow_progress["delivery"]["finalKm"], 1200)

