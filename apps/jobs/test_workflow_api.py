from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.invoices.models import Invoice
from apps.roles.models import Role
from .models import Job


class JobWorkflowApiTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Workflow Workshop", slug="workflow-api-test")
        branch = Branch.objects.create(company=company, name="Main", code="MAIN")
        role = Role.objects.create(
            company=company, name="Manager", code="MANAGER",
            permissions=["jobs.view", "jobs.create", "jobs.edit"],
        )
        user = User.objects.create(
            company=company, branch=branch, role=role,
            name="Manager", email="workflow-manager@test.local",
        )
        self.company = company
        self.branch = branch
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        created = self.client.post("/api/v1/jobs", {
            "customerName": "Customer",
            "customerPhone": "9876543210",
            "vehicleReg": "KL10AB1234",
            "vehicleInfo": "Maruti Swift",
            "status": "New",
        }, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.id = created.data["id"]
        self.url = f"/api/v1/jobs/{self.id}"

    def finish(self, stage, **extras):
        return self.client.post(
            f"{self.url}/workflow", {"stage": stage, **extras}, format="json"
        )

    def test_stage_order_and_resume_endpoint(self):
        initial = self.client.get(f"{self.url}/workflow")
        self.assertEqual(initial.status_code, 200, initial.data)
        self.assertEqual(initial.data["current"], "overview")
        self.assertIn("inspection", initial.data["locked"])

        skip = self.finish("inspection")
        self.assertEqual(skip.status_code, 400)
        jump = self.client.patch(f"{self.url}/status", {"status": "QC"}, format="json")
        self.assertEqual(jump.status_code, 400)

        overview = self.finish("overview")
        self.assertEqual(overview.status_code, 200, overview.data)
        self.assertEqual(overview.data["current"], "inspection")
        self.assertEqual(self.finish("overview").status_code, 400)

        start = self.client.post(f"{self.url}/inspection/start", {}, format="json")
        self.assertEqual(start.status_code, 200, start.data)
        inspection_check = self.client.patch(
            f"{self.url}/inspection/checklist",
            {"itemName": "Engine oil", "status": "Good"}, format="json",
        )
        self.assertEqual(inspection_check.status_code, 200, inspection_check.data)
        done = self.client.post(
            f"{self.url}/inspection/complete", {"needsApproval": True}, format="json"
        )
        self.assertEqual(done.status_code, 200, done.data)
        self.assertEqual(self.client.get(f"{self.url}/workflow").data["current"], "estimate")

        estimate = self.client.post(f"{self.url}/estimates", {
            "version": 1,
            "status": "Draft",
            "items": [],
            "subtotal": "250.00",
            "tax": "0.00",
            "total": "250.00",
        }, format="json")
        self.assertEqual(estimate.status_code, 201, estimate.data)
        self.assertEqual(self.finish("estimate").status_code, 400)
        approved = self.finish("estimate", approveEstimate=True)
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data["current"], "work")

        self.assertEqual(self.finish("work").status_code, 400)
        work = self.finish("work", confirmWorkDone=True)
        self.assertEqual(work.status_code, 200, work.data)
        self.assertEqual(work.data["current"], "qc")
        self.assertEqual(self.finish("qc").status_code, 400)

        qc = self.client.patch(
            self.url, {"qc": {"status": "Pass", "checklist": [
                {"id": "QC-1", "item": "Brake test", "status": "Pass"}
            ]}}, format="json"
        )
        self.assertEqual(qc.status_code, 200, qc.data)
        qc_done = self.finish("qc")
        self.assertEqual(qc_done.status_code, 200, qc_done.data)
        self.assertEqual(qc_done.data["current"], "invoice")
        self.assertEqual(self.finish("invoice").status_code, 400)

        job = Job.objects.get(pk=self.id)
        Invoice.objects.create(
            company=self.company, branch=self.branch, job=job,
            customer=job.customer, vehicle=job.vehicle,
            number="INV-WORKFLOW-001", kind="invoice",
            status="Finalized", date=timezone.localdate(), total="250.00",
        )
        finished = self.finish("invoice")
        self.assertEqual(finished.status_code, 200, finished.data)
        self.assertTrue(finished.data["finished"])
        self.assertEqual(Job.objects.get(pk=self.id).status, "Delivered")

    def test_quick_estimate_inspection_edit_and_approval_guard(self):
        self.assertEqual(self.finish("overview").status_code, 200)
        self.assertEqual(self.client.post(f"{self.url}/inspection/start", {}, format="json").status_code, 200)
        checked = self.client.patch(
            f"{self.url}/inspection/checklist",
            {"itemName": "Engine oil", "status": "Good"}, format="json",
        )
        self.assertEqual(checked.status_code, 200, checked.data)
        self.assertEqual(self.client.post(f"{self.url}/inspection/complete", {}, format="json").status_code, 200)

        quote = {
            "lines": [
                {"description": "Engine oil", "type": "Part", "quantity": 2, "unitPrice": 800},
                {"description": "Oil change labour", "type": "Labour", "quantity": 1, "unitPrice": 500},
            ],
            "discount": 100, "taxPercent": 18,
        }
        created = self.client.post(f"{self.url}/estimates/quick", quote, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(str(created.data["subtotal"]), "2100.00")
        self.assertEqual(str(created.data["tax"]), "360.00")
        self.assertEqual(str(created.data["total"]), "2360.00")
        estimate_id = created.data["id"]

        invalid = self.client.patch(
            f"{self.url}/estimates/quick/{estimate_id}",
            {**quote, "discount": 5000}, format="json",
        )
        self.assertEqual(invalid.status_code, 400)
        updated = self.client.patch(
            f"{self.url}/estimates/quick/{estimate_id}",
            {**quote, "discount": 200}, format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(str(updated.data["total"]), "2242.00")

        # Completed inspections can be corrected while Estimate is still active.
        edit = self.client.patch(
            f"{self.url}/inspection/checklist",
            {"itemName": "Engine oil", "status": "Needs Attention"}, format="json",
        )
        self.assertEqual(edit.status_code, 200, edit.data)
        self.assertIn("revisedAt", edit.data)
        stale = self.finish("estimate", approveEstimate=True)
        self.assertEqual(stale.status_code, 400, stale.data)

        revised = self.client.post(f"{self.url}/estimates/quick", quote, format="json")
        self.assertEqual(revised.status_code, 201, revised.data)
        approved = self.finish("estimate", approveEstimate=True)
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data["current"], "work")

        after = self.client.patch(
            f"{self.url}/inspection/checklist",
            {"itemName": "Engine oil", "status": "Good"}, format="json",
        )
        self.assertEqual(after.status_code, 400)
        frozen_quote = self.client.patch(
            f"{self.url}/estimates/quick/{revised.data['id']}", quote, format="json",
        )
        self.assertEqual(frozen_quote.status_code, 400)


    def test_quick_estimate_catalog_service_and_part_links(self):
        from apps.services.models import Service
        from apps.inventory.models import StockItem

        service = Service.objects.create(
            company=self.company, branch=self.branch,
            name="Brake cleaning", code="BR-CLN", price="650.00",
        )
        part = StockItem.objects.create(
            company=self.company, branch=self.branch,
            name="Brake pad set", sku="BR-PAD-01",
            selling_price="1450.00", on_hand="0",
        )

        self.assertEqual(self.finish("overview").status_code, 200)
        self.assertEqual(self.client.post(f"{self.url}/inspection/start", {}, format="json").status_code, 200)
        self.assertEqual(self.client.patch(
            f"{self.url}/inspection/checklist",
            {"itemName": "Brakes", "status": "Good"}, format="json",
        ).status_code, 200)
        self.assertEqual(self.client.post(f"{self.url}/inspection/complete", {}, format="json").status_code, 200)

        quote = {
            "lines": [
                {"description": service.name, "type": "Labour", "quantity": "1",
                 "unitPrice": "650", "catalogId": str(service.pk)},
                {"description": part.name, "type": "Part", "quantity": "2",
                 "unitPrice": "1450", "catalogId": str(part.pk)},
            ],
            "discount": "0", "taxPercent": "0",
        }
        response = self.client.post(f"{self.url}/estimates/quick", quote, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["items"][0]["catalogId"], str(service.pk))
        self.assertEqual(response.data["items"][1]["catalogId"], str(part.pk))
        self.assertEqual(str(response.data["total"]), "3550.00")
        part.refresh_from_db()
        self.assertEqual(str(part.on_hand), "0.00")  # Quote must never deduct stock.

        edited = self.client.patch(
            f"{self.url}/estimates/quick/{response.data['id']}", quote, format="json"
        )
        self.assertEqual(edited.status_code, 200, edited.data)
        self.assertEqual(edited.data["items"][1]["catalogId"], str(part.pk))

        invalid = self.client.post(f"{self.url}/estimates/quick", {
            **quote,
            "lines": [{**quote["lines"][0], "catalogId": str(part.pk)}]
        }, format="json")
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("catalogId", invalid.data)

        external_company = Company.objects.create(name="Other Workshop", slug="other-workshop-catalog")
        external_part = StockItem.objects.create(
            company=external_company, name="Other company's part", sku="EXTERNAL-001"
        )
        foreign = self.client.post(f"{self.url}/estimates/quick", {
            **quote,
            "lines": [{**quote["lines"][1], "catalogId": str(external_part.pk)}]
        }, format="json")
        self.assertEqual(foreign.status_code, 400)
        self.assertIn("catalogId", foreign.data)

