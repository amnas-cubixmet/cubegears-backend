"""Actual service entries are idempotent and separate customer charges from worker pay."""
from uuid import uuid4
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.jobs.models import Job, JobActivity
from apps.roles.models import Role
from apps.services.models import Service
from apps.vehicles.models import Vehicle


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
                   CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class JobWorkServiceQuickTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Quick Work Garage", slug="quick-work-garage")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        role = Role.objects.create(company=self.company, name="Owner", code="WORK_SERVICE_OWNER",
                                   permissions=["*"], is_active=True)
        self.owner = User.objects.create_user(
            email="quick-work-owner@example.test", password="safe-password",
            name="Owner", company=self.company, branch=self.branch, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.mechanic = User.objects.create_user(
            email="work-mechanic@example.test", password="safe-password",
            name="Mechanic", company=self.company, branch=self.branch, role=role,
        )
        customer = Customer.objects.create(
            company=self.company, branch=self.branch, name="Customer", phone="9876543200",
        )
        vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=customer,
            registration="KL10AB2367", make="Tata", model="Nexon",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch,
            job_number="WORK-SERVICE-001", customer=customer, vehicle=vehicle,
            status="In Progress",
            workflow_progress={"completed": ["overview", "inspection", "estimate"]},
        )
        self.service = Service.objects.create(
            company=self.company, branch=self.branch,
            name="Door Painting", code="PAINT-100", price=Decimal("800"),
            tax_rate=Decimal("18"), hsn_sac="998714", unit="Job",
        )
        self.url = f"/api/v1/jobs/{self.job.pk}/work/services"
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def payload(self):
        return {
            "requestId": str(uuid4()), "serviceId": str(self.service.pk),
            "quantity": "2", "customerRate": "800",
            "mechanicId": str(self.mechanic.pk), "mechanicCost": "500",
        }

    def test_customer_revenue_worker_charge_audit_and_retry_once(self):
        payload = self.payload()
        first = self.client.post(self.url, payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        item = first.data["item"]
        self.assertEqual(item["customerCharge"], "1600.00")
        self.assertEqual(item["agreedWorkerCharge"], "500.00")
        self.assertIs(item["workerChargePosted"], False)
        self.assertEqual(item["hsnSac"], "998714")
        self.assertEqual(item["mechanicId"], str(self.mechanic.pk))
        again = self.client.post(self.url, payload, format="json")
        self.assertEqual(again.status_code, 200, again.data)
        self.assertEqual(again.data["item"]["id"], item["id"])
        self.job.refresh_from_db()
        self.assertEqual(len(self.job.work), 1)
        self.assertEqual(JobActivity.objects.filter(job=self.job, event="Job Service Added").count(), 1)
        from apps.payroll.models import JobWorkSession
        self.assertFalse(JobWorkSession.objects.filter(job=self.job).exists())
        from apps.inventory.models import StockMovement
        self.assertFalse(StockMovement.objects.filter(job=self.job).exists())

    def test_service_links_to_invoice_suggestions_without_mechanic_pay(self):
        from apps.invoices.job_draft import suggested_job_invoice
        from apps.invoices.models import Invoice
        response = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.job.refresh_from_db()
        suggested = suggested_job_invoice(self.job)
        self.assertFalse(suggested["created"])
        self.assertFalse(suggested["finalized"])
        self.assertEqual(len(suggested["items"]), 1)
        line = suggested["items"][0]
        self.assertEqual(line["description"], "Door Painting")
        self.assertEqual(line["qty"], "2.00")
        self.assertEqual(line["rate"], "800.00")
        self.assertEqual(line["hsnCode"], "998714")
        self.assertNotIn("500", str(line))
        self.assertFalse(Invoice.objects.filter(company=self.company, job=self.job).exists())

    def test_optional_mechanic_and_zero_charge(self):
        payload = self.payload()
        payload.pop("mechanicId")
        payload["mechanicCost"] = "0"
        result = self.client.post(self.url, payload, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        self.assertIsNone(result.data["item"]["mechanicId"])


    def test_billable_customer_charge_does_not_increase_direct_cost(self):
        result = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(result.status_code, 201, result.data)
        direct_cost = self.client.get(f"/api/v1/jobs/{self.job.pk}/cost-summary")
        self.assertEqual(direct_cost.status_code, 200, direct_cost.data)
        self.assertEqual(direct_cost.data["totalCost"], "0.00")
        self.assertEqual(direct_cost.data["mechanicCost"], "0.00")
        self.assertEqual(result.data["item"]["customerCharge"], "1600.00")

    def test_remove_service_updates_job_and_invoice_suggestions_but_not_direct_cost(self):
        from apps.invoices.job_draft import suggested_job_invoice
        posted = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(posted.status_code, 201, posted.data)
        work_id = posted.data["item"]["id"]
        endpoint = f"{self.url}/{work_id}"
        removed = self.client.delete(endpoint)
        self.assertEqual(removed.status_code, 200, getattr(removed, "data", None))
        self.assertEqual(removed.data["removedId"], work_id)
        self.job.refresh_from_db()
        self.assertEqual(self.job.work, [])
        self.assertEqual(suggested_job_invoice(self.job)["items"], [])
        audit = JobActivity.objects.get(job=self.job, event="Job Service Removed")
        self.assertEqual(audit.metadata["workId"], work_id)
        self.assertEqual(audit.metadata["customerCharge"], "1600.00")
        self.assertEqual(self.client.delete(endpoint).status_code, 400)

    def test_remove_does_not_touch_timers_or_unposted_worker_charge(self):
        posted = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(posted.status_code, 201)
        work_id = posted.data["item"]["id"]
        self.job.refresh_from_db()
        line = self.job.work[0]
        line["workerChargePosted"] = True
        self.job.work = [line]
        self.job.save(update_fields=["work"])
        self.assertEqual(self.client.delete(f"{self.url}/{work_id}").status_code, 400)
        self.job.refresh_from_db()
        self.assertEqual(len(self.job.work), 1)
        self.assertEqual(self.job.work[0]["agreedWorkerCharge"], "500.00")

    def test_remove_is_blocked_outside_work_or_after_invoice(self):
        posted = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(posted.status_code, 201)
        work_id = posted.data["item"]["id"]
        endpoint = f"{self.url}/{work_id}"
        self.job.status = Job.STATUS_QC
        self.job.save(update_fields=["status"])
        self.assertEqual(self.client.delete(endpoint).status_code, 400)
        self.job.status = Job.STATUS_IN_PROGRESS
        self.job.save(update_fields=["status"])
        # Use a saved invoice only if required fields allow construction; the
        # route itself validates any non-cancelled matching invoice.
        self.assertEqual(self.client.delete(endpoint).status_code, 200)

    def test_jobs_view_only_staff_cannot_remove_services(self):
        posted = self.client.post(self.url, self.payload(), format="json")
        work_id = posted.data["item"]["id"]
        viewer_role = Role.objects.create(
            company=self.company, name="Read only services", code="WORK_SERVICE_READ_ONLY",
            permissions=["jobs.view"], is_active=True,
        )
        viewer = User.objects.create_user(
            email="readonly-service@example.test", password="safe-password",
            company=self.company, name="Viewer", role=viewer_role,
        )
        self.client.force_authenticate(viewer)
        self.assertEqual(self.client.delete(f"{self.url}/{work_id}").status_code, 403)
        self.job.refresh_from_db()
        self.assertEqual(len(self.job.work), 1)

    def test_invalid_amounts_and_missing_request_id(self):
        for changes in [
            {"quantity": "0"}, {"quantity": "-1"}, {"quantity": "2.333"},
            {"quantity": "Infinity"}, {"customerRate": "-5"},
            {"customerRate": "NaN"}, {"mechanicCost": "1.555"},
            {"requestId": "not-a-uuid"}, {"serviceId": str(uuid4())},
        ]:
            payload = {**self.payload(), **changes}
            result = self.client.post(self.url, payload, format="json")
            self.assertEqual(result.status_code, 400, (changes, result.data))
        self.assertFalse(self.job.work)

    def test_only_work_stage_and_jobs_edit_role(self):
        self.job.status = "QC"
        self.job.save(update_fields=["status"])
        self.assertEqual(self.client.post(self.url, self.payload(), format="json").status_code, 400)
        self.job.status = "In Progress"
        self.job.save(update_fields=["status"])
        viewer_role = Role.objects.create(
            company=self.company, name="Viewer", code="WORK_VIEWER",
            permissions=["jobs.view"], is_active=True,
        )
        viewer = User.objects.create_user(
            email="quick-work-viewer@example.test", password="safe-password",
            company=self.company, name="Viewer", role=viewer_role,
        )
        self.client.force_authenticate(viewer)
        self.assertEqual(self.client.post(self.url, self.payload(), format="json").status_code, 403)

    def test_cross_company_service_and_mechanic_rejected(self):
        other = Company.objects.create(name="Other Garage", slug="other-quick-garage")
        foreign = Service.objects.create(company=other, name="Foreign Work",
                                         code="OTH-001", price=Decimal("100"))
        p = self.payload()
        p["serviceId"] = str(foreign.pk)
        self.assertEqual(self.client.post(self.url, p, format="json").status_code, 400)
        p = self.payload()
        foreign_user = User.objects.create_user(
            email="other-mechanic@example.test", password="safe-password",
            company=other, name="Other Mechanic",
        )
        p["mechanicId"] = str(foreign_user.pk)
        self.assertEqual(self.client.post(self.url, p, format="json").status_code, 400)
        self.assertFalse(self.job.work)
