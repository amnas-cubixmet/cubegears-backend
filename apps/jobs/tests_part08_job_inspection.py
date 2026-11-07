"""Part 08 integration: idempotent Job Cards and real private inspection evidence."""

from io import BytesIO
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.jobs.models import Job, JobActivity
from apps.roles.models import Role
from apps.vehicles.models import Vehicle, VehicleInspectionPhoto


def image_upload():
    data = BytesIO()
    Image.new("RGB", (32, 25), (100, 125, 165)).save(data, format="PNG")
    return SimpleUploadedFile("check-in.png", data.getvalue(), content_type="image/png")


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class JobInspectionBuildTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Part 08 Garage", slug="part-08-garage")
        self.other = Company.objects.create(name="Part 08 Other", slug="part-08-other")
        self.branch = Branch.objects.create(
            company=self.company, name="Main Workshop", code="MAIN", is_active=True,
        )
        role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER_PART08",
            permissions=["*"], is_system=True, is_active=True,
        )
        self.owner = User.objects.create_user(
            email="part08-owner@example.test", password="StrongPass123!",
            name="Owner", company=self.company, role=role, branch=self.branch,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Inspection Customer", phone="+919888887777",
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch,
            customer=self.customer, registration="KL10AB1001",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch,
            customer=self.customer, vehicle=self.vehicle,
            job_number="TEST-PART08-001",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def open_inspection(self):
        url = f"/api/v1/jobs/{self.job.pk}/workflow"
        reply = self.client.post(url, {"stage": "overview"}, format="json")
        self.assertEqual(reply.status_code, 200, getattr(reply, "data", None))
        reply = self.client.post(
            f"/api/v1/jobs/{self.job.pk}/inspection/start", {}, format="json",
        )
        self.assertEqual(reply.status_code, 200, getattr(reply, "data", None))

    def test_job_create_retry_returns_same_job_customer_and_vehicle(self):
        payload = {
            "customer": str(self.customer.pk),
            "customerName": self.customer.name,
            "customerPhone": self.customer.phone,
            "vehicleReg": "KL10AB2002",
            "vehicleMake": "Honda", "vehicleModel": "City",
            "branchId": str(self.branch.pk),
            "clientRequestId": "part08-job-request-key-000001",
            "status": "New",
        }
        first = self.client.post("/api/v1/jobs", payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        second = self.client.post("/api/v1/jobs", payload, format="json")
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(Job.objects.filter(company=self.company).count(), 2)
        self.assertEqual(Vehicle.objects.filter(company=self.company).count(), 2)
        self.assertEqual(Customer.objects.filter(company=self.company).count(), 1)
        job = Job.objects.get(pk=first.data["id"])
        self.assertEqual(job.workflow_progress["createRequestId"], payload["clientRequestId"])
        self.assertEqual(JobActivity.objects.filter(job=job, event="Job Created").count(), 1)

    def test_new_job_number_skips_existing_number_instead_of_failing(self):
        from django.utils import timezone
        blocker = f"JOB-{timezone.now().year}-00002"
        Job.objects.create(
            company=self.company, branch=self.branch,
            customer=self.customer, vehicle=self.vehicle, job_number=blocker,
        )
        payload = {
            "customer": str(self.customer.pk), "vehicle": str(self.vehicle.pk),
            "status": "New",
        }
        saved = self.client.post("/api/v1/jobs", payload, format="json")
        self.assertEqual(saved.status_code, 201, saved.data)
        self.assertNotEqual(saved.data["jobNumber"], blocker)
        self.assertEqual(Job.objects.filter(company=self.company).count(), 3)

    def test_inspection_finding_never_trusts_client_id_or_approval_flags(self):
        self.open_inspection()
        endpoint = f"/api/v1/jobs/{self.job.pk}/inspection/findings"
        reply = self.client.post(endpoint, {
            "id": "FND-FORGED", "description": "Front brake vibration",
            "severity": "High", "estimatedCost": "350.25",
            "addedToEstimate": True, "estimateStatus": "Approved",
            "dateTime": "1999-01-01", "recommendedAction": "Inspect pads",
        }, format="json")
        self.assertEqual(reply.status_code, 201, reply.data)
        self.assertNotEqual(reply.data["id"], "FND-FORGED")
        self.assertFalse(reply.data["addedToEstimate"])
        self.assertEqual(reply.data["estimateStatus"], "Not Added")
        self.assertNotEqual(reply.data["dateTime"], "1999-01-01")
        self.assertEqual(reply.data["estimatedCost"], "350.25")
        invalid = self.client.post(endpoint, {
            "description": "Brakes", "severity": "Critical",
            "estimatedCost": "-50",
        }, format="json")
        self.assertEqual(invalid.status_code, 400, invalid.data)
        self.job.refresh_from_db()
        self.assertEqual(len(self.job.inspection["findings"]), 1)
        finding_id = reply.data["id"]
        changed = self.client.put(f"{endpoint}/{finding_id}", {
            "description": "Front brake pads worn",
            "id": "FND-FAKE", "addedToEstimate": True,
            "dateTime": "1999-01-01",
        }, format="json")
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data["id"], finding_id)
        self.assertFalse(changed.data["addedToEstimate"])

    def test_diagnostic_id_and_timestamp_are_server_controlled(self):
        self.open_inspection()
        endpoint = f"/api/v1/jobs/{self.job.pk}/inspection/diagnostic"
        reply = self.client.post(endpoint, {
            "id": "OBD-FORGED", "code": "p0300",
            "description": "Misfire", "recordedById": "foreign",
            "dateTime": "1998-01-01",
        }, format="json")
        self.assertEqual(reply.status_code, 201, reply.data)
        self.assertNotEqual(reply.data["id"], "OBD-FORGED")
        self.assertEqual(reply.data["code"], "P0300")
        self.assertEqual(reply.data["recordedById"], str(self.owner.pk))
        bad = self.client.post(endpoint, {
            "code": "<script>", "description": "Invalid",
        }, format="json")
        self.assertEqual(bad.status_code, 400, bad.data)

    def test_real_photo_upload_then_reference_only_never_embeds_image_data(self):
        self.open_inspection()
        endpoint = f"/api/v1/jobs/{self.job.pk}/inspection/photos"
        without_upload = self.client.post(endpoint, {
            "url": "data:image/png;base64,ABC123", "caption": "Forged image",
        }, format="json")
        self.assertEqual(without_upload.status_code, 400, without_upload.data)
        with TemporaryDirectory() as private_dir, override_settings(
            PRIVATE_JOB_MEDIA_ROOT=private_dir,
        ):
            saved = self.client.post(
                f"/api/v1/jobs/{self.job.pk}/inspection-photos",
                {"image": image_upload(), "category": "damage", "phase": "before",
                 "caption": "Check-in scratch"}, format="multipart",
            )
            self.assertEqual(saved.status_code, 201, getattr(saved, "data", None))
            self.assertFalse(saved.data["published"])
            photo_id = saved.data["id"]
            linked = self.client.post(endpoint, {
                "photoId": photo_id,
                "url": "data:image/png;base64,UNTRUSTED",
                "caption": "Forged caption",
            }, format="json")
            self.assertEqual(linked.status_code, 201, linked.data)
            self.assertEqual(linked.data["privatePhotoId"], photo_id)
            self.assertEqual(linked.data["caption"], "Check-in scratch")
            repeated = self.client.post(endpoint, {"photoId": photo_id}, format="json")
            self.assertEqual(repeated.status_code, 200, repeated.data)
            self.job.refresh_from_db()
            self.assertEqual(len(self.job.inspection["photos"]), 1)
            self.assertNotIn("base64", str(self.job.inspection))
            self.assertTrue(VehicleInspectionPhoto.objects.filter(pk=photo_id).exists())

    def test_cannot_link_image_uploaded_to_another_job(self):
        self.open_inspection()
        other_job = Job.objects.create(
            company=self.company, branch=self.branch,
            customer=self.customer, vehicle=self.vehicle,
            job_number="TEST-PART08-OTHER",
        )
        with TemporaryDirectory() as private_dir, override_settings(
            PRIVATE_JOB_MEDIA_ROOT=private_dir,
        ):
            saved = self.client.post(
                f"/api/v1/jobs/{other_job.pk}/inspection-photos",
                {"image": image_upload()}, format="multipart",
            )
            self.assertEqual(saved.status_code, 201, saved.data)
            denied = self.client.post(
                f"/api/v1/jobs/{self.job.pk}/inspection/photos",
                {"photoId": saved.data["id"]}, format="json",
            )
            self.assertEqual(denied.status_code, 400, denied.data)

    def test_generic_job_patch_cannot_replace_customer_vehicle_or_job_number(self):
        outsider = Customer.objects.create(
            company=self.other, name="Other customer", phone="+919123456789",
        )
        for field, value in (
            ("customer", str(outsider.pk)),
            ("jobNumber", "MANIPULATED-001"),
        ):
            reply = self.client.patch(
                f"/api/v1/jobs/{self.job.pk}", {field: value}, format="json",
            )
            self.assertEqual(reply.status_code, 400, reply.data)
        self.job.refresh_from_db()
        self.assertEqual(self.job.customer_id, self.customer.pk)
        self.assertEqual(self.job.job_number, "TEST-PART08-001")

    def test_delivered_job_lock_does_not_block_feedback(self):
        self.job.status = Job.STATUS_DELIVERED
        self.job.save(update_fields=["status"])
        denied = self.client.patch(
            f"/api/v1/jobs/{self.job.pk}", {"priority": "Urgent"}, format="json",
        )
        self.assertEqual(denied.status_code, 400, denied.data)
        allowed = self.client.patch(
            f"/api/v1/jobs/{self.job.pk}",
            {"customerRating": 5, "customerFeedback": "Great service"},
            format="json",
        )
        self.assertEqual(allowed.status_code, 200, allowed.data)
        self.job.refresh_from_db()
        self.assertEqual(self.job.customer_rating, 5)
