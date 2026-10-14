"""Jobs Control Panel UI: real KPI counts, filters and tenant-safe editor."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.vehicles.models import Vehicle
from apps.jobs.models import Job


class JobsManagementUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="jobs-ui-admin@example.com", name="Jobs UI Admin",
            password="Secure-jobs-ui-admin-2026!",
        )
        cls.alpha = Company.objects.create(name="Alpha Garage", slug="job-ui-alpha")
        cls.beta = Company.objects.create(name="Beta Garage", slug="job-ui-beta")
        cls.customer = Customer.objects.create(
            company=cls.alpha, name="Alpha Customer", phone="9876543210"
        )
        cls.vehicle = Vehicle.objects.create(
            company=cls.alpha, customer=cls.customer, registration="KL10AB1234"
        )
        cls.other_customer = Customer.objects.create(
            company=cls.beta, name="Beta Customer", phone="9876543211"
        )
        cls.other_vehicle = Vehicle.objects.create(
            company=cls.beta, customer=cls.other_customer, registration="KL10CD5678"
        )
        cls.started = Job.objects.create(
            company=cls.alpha, job_number="JOB-100",
            customer=cls.customer, vehicle=cls.vehicle,
            status=Job.STATUS_IN_PROGRESS, priority="High",
            estimate_total=Decimal("1200.00")
        )
        cls.waiting = Job.objects.create(
            company=cls.alpha, job_number="JOB-200",
            customer=cls.customer, vehicle=cls.vehicle,
            status=Job.STATUS_WAITING_PARTS, priority="Normal",
        )
        cls.delivered = Job.objects.create(
            company=cls.beta, job_number="JOB-300",
            customer=cls.other_customer, vehicle=cls.other_vehicle,
            status=Job.STATUS_DELIVERED, priority="Low",
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_job_directory_has_live_kpis_and_company_layout(self):
        response = self.client.get(reverse("panel:jobs"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Job Cards Management")
        self.assertContains(response, "Waiting for Parts")
        self.assertContains(response, "Job Card Directory")
        self.assertContains(response, "jobs-ui.css")
        self.assertContains(response, 'class="user-stat-value"', count=4)
        self.assertContains(response, "JOB-100")
        self.assertContains(response, "JOB-200")
        self.assertContains(response, "JOB-300")
        self.assertEqual(response.context["total_jobs"], 3)
        self.assertEqual(response.context["in_progress_jobs"], 1)
        self.assertEqual(response.context["waiting_parts_jobs"], 1)
        self.assertEqual(response.context["delivered_jobs"], 1)
        self.assertIsNotNone(finders.find("control_panel/jobs-ui.css"))

    def test_search_company_status_priority_filters_compose(self):
        url = reverse("panel:jobs")
        result = self.client.get(url, {
            "company": str(self.alpha.pk), "q": "JOB-100",
            "status": Job.STATUS_IN_PROGRESS, "priority": "High",
        })
        self.assertEqual(result.context["rows"].paginator.count, 1)
        self.assertContains(result, "JOB-100")
        self.assertNotContains(result, "JOB-200")
        self.assertNotContains(result, "JOB-300")
        self.assertEqual(result.context["total_jobs"], 3)
        waiting = self.client.get(url, {"status": Job.STATUS_WAITING_PARTS})
        self.assertEqual(waiting.context["rows"].paginator.count, 1)
        by_vehicle = self.client.get(url, {"q": "KL10CD5678"})
        self.assertEqual(by_vehicle.context["rows"].paginator.count, 1)
        invalid = self.client.get(url, {"status": "Invalid", "priority": "Not Real"})
        self.assertEqual(invalid.context["rows"].paginator.count, 3)
        self.assertEqual(invalid.context["status_value"], "")
        self.assertEqual(invalid.context["priority"], "")

    def test_add_edit_forms_keep_original_fields_and_local_datetime(self):
        create = self.client.get(reverse("panel:job-create"))
        self.assertTemplateUsed(create, "control_panel/job_form.html")
        for title in (
            "Job Identification", "Customer &amp; Vehicle",
            "Assignment &amp; Timeline", "Inspection &amp; Workflow",
            "Estimate &amp; Notes",
        ):
            self.assertContains(create, title)
        self.assertContains(create, 'name="csrfmiddlewaretoken"')
        for field in (
            "company", "branch", "job_number", "customer", "vehicle",
            "advisor", "technician", "status", "priority", "odometer",
            "fuel_level", "promised_at", "delivered_at", "complaints",
            "inspection", "work", "qc", "parts_workflow", "notes",
            "estimate_total", "labour_total", "parts_total",
        ):
            self.assertContains(create, f'name="{field}"')

        self.started.promised_at = timezone.now() + timedelta(days=5)
        self.started.save(update_fields=["promised_at"])
        edit = self.client.get(reverse("panel:job-edit", args=[self.started.pk]))
        self.assertTemplateUsed(edit, "control_panel/job_form.html")
        self.assertContains(edit, 'type="datetime-local"')
        self.assertContains(edit, "Save Changes")

    def test_create_and_edit_keep_real_job_data(self):
        payload = {
            "company": str(self.alpha.pk),
            "job_number": "JOB-400",
            "customer": str(self.customer.pk),
            "vehicle": str(self.vehicle.pk),
            "status": Job.STATUS_NEW, "priority": "Normal",
            "odometer": "42000", "fuel_level": "Half",
            "complaints": "[]", "inspection": "{}",
            "work": "[]", "qc": "{}", "parts_workflow": "{}",
            "estimate_total": "1000.00",
            "labour_total": "250.00", "parts_total": "750.00",
        }
        self.assertRedirects(
            self.client.post(reverse("panel:job-create"), payload),
            reverse("panel:jobs"),
        )
        job = Job.objects.get(company=self.alpha, job_number="JOB-400")
        self.assertEqual(job.estimate_total, Decimal("1000.00"))
        self.assertEqual(job.complaints, [])
        payload["priority"] = "Urgent"
        payload["notes"] = "Customer requested quick turnaround"
        self.assertRedirects(
            self.client.post(reverse("panel:job-edit", args=[job.pk]), payload),
            reverse("panel:jobs"),
        )
        job.refresh_from_db()
        self.assertEqual(job.priority, "Urgent")
        self.assertEqual(job.notes, "Customer requested quick turnaround")

    def test_cross_company_customer_and_vehicle_are_rejected(self):
        payload = {
            "company": str(self.alpha.pk), "job_number": "JOB-CROSS",
            "customer": str(self.other_customer.pk),
            "vehicle": str(self.other_vehicle.pk),
            "status": Job.STATUS_NEW, "priority": "Normal",
        }
        response = self.client.post(reverse("panel:job-create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertFalse(Job.objects.filter(job_number="JOB-CROSS").exists())

    def test_job_details_display_saved_estimate_and_status(self):
        response = self.client.get(reverse("panel:job-detail", args=[self.started.pk]))
        self.assertContains(response, 'class="panel-card jobs-profile-header"')
        self.assertContains(response, "JOB-100")
        self.assertContains(response, "In Progress")
        self.assertContains(response, "1200.00")
        self.assertContains(response, "Alpha Customer")

    def test_anonymous_views_redirect(self):
        self.client.logout()
        for name in ("panel:jobs", "panel:job-create"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)
        self.assertEqual(
            self.client.get(reverse("panel:job-detail", args=[self.started.pk])).status_code,
            302
        )
