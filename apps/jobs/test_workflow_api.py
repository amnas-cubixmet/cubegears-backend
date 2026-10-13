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


    def _advance_to_estimate_for_guard_test(self):
        self.assertEqual(self.finish("overview").status_code, 200)
        self.assertEqual(self.client.post(f"{self.url}/inspection/start", {}, format="json").status_code, 200)
        self.assertEqual(self.client.patch(
            f"{self.url}/inspection/checklist",
            {"itemName": "Brakes", "status": "Good"}, format="json",
        ).status_code, 200)
        self.assertEqual(self.client.post(
            f"{self.url}/inspection/complete", {}, format="json",
        ).status_code, 200)

    def test_estimate_rejection_only_applies_to_latest_unapproved_draft(self):
        self._advance_to_estimate_for_guard_test()
        payload = {"lines": [
            {"description": "Brake service", "type": "Labour", "quantity": "1", "unitPrice": "500"},
        ], "discount": "0", "taxPercent": "0"}
        first = self.client.post(f"{self.url}/estimates/quick", payload, format="json")
        second = self.client.post(f"{self.url}/estimates/quick", payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)

        spoofed = self.client.post(f"{self.url}/estimates", {
            "version": 99, "status": "Approved", "items": [],
        }, format="json")
        self.assertEqual(spoofed.status_code, 400, spoofed.data)

        old_rejection = self.client.post(
            f"{self.url}/estimates/{first.data['id']}/decision",
            {"decision": "Rejected"}, format="json",
        )
        self.assertEqual(old_rejection.status_code, 400, old_rejection.data)

        rejected = self.client.post(
            f"{self.url}/estimates/{second.data['id']}/decision",
            {"decision": "Rejected"}, format="json",
        )
        self.assertEqual(rejected.status_code, 200, rejected.data)
        self.assertEqual(rejected.data["status"], "Rejected")
        self.assertEqual(self.finish("estimate", approveEstimate=True).status_code, 400)
        history = self.client.get(f"{self.url}/activity")
        self.assertTrue(any(event["event"] == "Estimate Rejected" for event in history.data))

        third = self.client.post(f"{self.url}/estimates/quick", payload, format="json")
        self.assertEqual(third.status_code, 201, third.data)
        self.assertEqual(self.finish("estimate", approveEstimate=True).status_code, 200)
        after_approval = self.client.post(
            f"{self.url}/estimates/{third.data['id']}/decision",
            {"decision": "Rejected"}, format="json",
        )
        self.assertEqual(after_approval.status_code, 400, after_approval.data)
        self.assertEqual(self.client.post(
            f"{self.url}/estimates", {"status": "Draft", "items": []}, format="json",
        ).status_code, 400)

    def test_quick_estimate_rejects_nonfinite_overprecision_and_unsupported_types(self):
        self._advance_to_estimate_for_guard_test()
        payload = {"lines": [
            {"description": "Oil", "type": "Part", "quantity": "1", "unitPrice": "500"},
        ], "discount": "0", "taxPercent": "18"}
        for field, invalid in [
            ("quantity", "NaN"), ("quantity", "1.001"), ("unitPrice", "Infinity"),
            ("unitPrice", "1.234"), ("type", "MadeUp"),
        ]:
            quote = {**payload, "lines": [{**payload["lines"][0], field: invalid}]}
            result = self.client.post(f"{self.url}/estimates/quick", quote, format="json")
            self.assertEqual(result.status_code, 400, (field, invalid, result.data))
        for field, invalid in [("discount", "NaN"), ("discount", "0.001"),
                               ("taxPercent", "Infinity"), ("taxPercent", "0.001")]:
            quote = {**payload, field: invalid}
            result = self.client.post(f"{self.url}/estimates/quick", quote, format="json")
            self.assertEqual(result.status_code, 400, (field, invalid, result.data))
        self.assertEqual(self.client.post(
            f"{self.url}/estimates/quick", payload, format="json",
        ).status_code, 201)

    def test_complaint_status_automatically_follows_work_and_qc(self):
        complaint = {
            "id": "CMP-AUTO-1", "description": "Engine warning light",
            "wording": "Engine warning light", "status": "Completed",
        }
        started = self.client.patch(
            self.url, {"complaints": [complaint]}, format="json",
        )
        self.assertEqual(started.status_code, 200, started.data)
        self.assertEqual(started.data["complaints"][0]["status"], "Open")
        self.assertEqual(Job.objects.get(pk=self.id).complaints[0]["status"], "Open")

        self.assertEqual(self.finish("overview").status_code, 200)
        self.assertEqual(self.client.post(
            f"{self.url}/inspection/start", {}, format="json",
        ).status_code, 200)
        self.assertEqual(self.client.patch(
            f"{self.url}/inspection/checklist",
            {"itemName": "Engine oil", "status": "Good"}, format="json",
        ).status_code, 200)
        self.assertEqual(self.client.post(
            f"{self.url}/inspection/complete", {}, format="json",
        ).status_code, 200)
        estimate = self.client.post(f"{self.url}/estimates", {
            "version": 1, "status": "Draft", "items": [],
            "subtotal": "100", "tax": "0", "total": "100",
        }, format="json")
        self.assertEqual(estimate.status_code, 201, estimate.data)

        in_progress = self.finish("estimate", approveEstimate=True)
        self.assertEqual(in_progress.status_code, 200, in_progress.data)
        self.assertEqual(in_progress.data["job"]["complaints"][0]["status"], "In Progress")
        self.assertEqual(Job.objects.get(pk=self.id).complaints[0]["status"], "In Progress")

        # Status is read-only even through a direct PATCH of the complaint JSON.
        manual = self.client.patch(self.url, {
            "complaints": [{**complaint, "status": "Open"}]
        }, format="json")
        self.assertEqual(manual.status_code, 200, manual.data)
        self.assertEqual(manual.data["complaints"][0]["status"], "In Progress")

        work = self.finish("work", confirmWorkDone=True)
        self.assertEqual(work.status_code, 200, work.data)
        self.assertEqual(work.data["job"]["complaints"][0]["status"], "In Progress")

        qc = self.client.patch(
            self.url, {"qc": {"status": "Pass", "checklist": [
                {"id": "QC-1", "item": "Road test", "status": "Pass"}
            ]}}, format="json",
        )
        self.assertEqual(qc.status_code, 200, qc.data)
        complete = self.finish("qc")
        self.assertEqual(complete.status_code, 200, complete.data)
        self.assertEqual(complete.data["job"]["complaints"][0]["status"], "Completed")
        self.assertEqual(Job.objects.get(pk=self.id).complaints[0]["status"], "Completed")
        self.assertEqual(
            self.client.get(self.url).data["complaints"][0]["status"], "Completed",
        )

    def test_complaint_legacy_status_is_normalized_on_read(self):
        job = Job.objects.get(pk=self.id)
        job.complaints = [{"id": "CMP-OLD", "description": "Brake noise", "status": "Completed"}]
        job.save(update_fields=["complaints"])
        data = self.client.get(self.url)
        self.assertEqual(data.status_code, 200)
        self.assertEqual(data.data["complaints"][0]["status"], "Open")


    def test_activity_endpoint_records_real_job_changes(self):
        initial = self.client.get(f"{self.url}/activity")
        self.assertEqual(initial.status_code, 200, initial.data)
        self.assertTrue(any(row["event"] == "Job Created" for row in initial.data))

        updates = self.client.patch(self.url, {
            "workUpdates": [{"id": "UP-1", "note": "Found worn brake pads", "type": "Inspection"}],
            "complaints": [{"id": "C-1", "description": "Brake noise"}],
        }, format="json")
        self.assertEqual(updates.status_code, 200, updates.data)
        events = self.client.get(f"{self.url}/activity")
        self.assertEqual(events.status_code, 200, events.data)
        names = [row["event"] for row in events.data]
        self.assertIn("Customer Complaints Updated", names)
        self.assertIn("Technician Updates Saved", names)
        updated = next(row for row in events.data if row["event"] == "Customer Complaints Updated")
        self.assertIn("created_at", updated)
        self.assertEqual(updated["actorName"], "Manager")
        self.assertEqual(updated["metadata"]["count"], 1)

        # A failed write does not fabricate audit history.
        before = len(events.data)
        rejected = self.client.patch(self.url, {
            "customerRating": 7
        }, format="json")
        self.assertEqual(rejected.status_code, 400)
        after = self.client.get(f"{self.url}/activity")
        self.assertEqual(len(after.data), before)

    def test_activity_rejects_cross_company_job_access(self):
        from apps.customers.models import Customer
        from apps.vehicles.models import Vehicle
        other = Company.objects.create(name="Other Activity Shop", slug="activity-other")
        customer = Customer.objects.create(company=other, name="Another Customer", phone="9999999999")
        vehicle = Vehicle.objects.create(company=other, customer=customer, registration="KL99AB4444")
        another_job = Job.objects.create(
            company=other, customer=customer, vehicle=vehicle, job_number="OTHER-ACTIVITY-1"
        )
        response = self.client.get(f"/api/v1/jobs/{another_job.pk}/activity")
        self.assertEqual(response.status_code, 404)
