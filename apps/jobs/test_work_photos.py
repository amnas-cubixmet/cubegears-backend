from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.roles.models import Role
from apps.vehicles.models import Vehicle
from apps.jobs.models import Job, JobPhoto


class JobWorkPhotoTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Photo Workshop", slug="photo-workshop")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        self.role = Role.objects.create(
            company=self.company, name="Manager", code="MANAGER",
            permissions=["jobs.view", "jobs.create", "jobs.edit"],
        )
        self.user = User.objects.create(
            company=self.company, branch=self.branch, role=self.role,
            name="Mechanic", email="mechanic@photo.local",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        job = self.client.post("/api/v1/jobs", {
            "customerName": "Photo Customer",
            "customerPhone": "9876543210",
            "vehicleReg": "KL10AA2001",
            "vehicleInfo": "Swift",
            "status": "New",
        }, format="json")
        self.assertEqual(job.status_code, 201, job.data)
        self.job_id = job.data["id"]
        self.base = f"/api/v1/jobs/{self.job_id}"

    def tearDown(self):
        # Uploaded files are intentionally not deleted with DB rows.
        # Clean up filesystem files written during API tests.
        for photo in JobPhoto.objects.filter(job_id=self.job_id):
            if photo.file:
                photo.file.delete(save=False)

    def image(self, body=None, content_type="image/jpeg"):
        # The upload API validates signatures without requiring Pillow.
        return SimpleUploadedFile(
            "repair.jpg",
            body if body is not None else b"\xff\xd8\xff\xe0" + b"jpeg-test-data",
            content_type=content_type,
        )

    def upload(self, image=None, caption="Brake pads replaced"):
        return self.client.post(
            f"{self.base}/photos/upload",
            {"image": image or self.image(), "caption": caption},
            format="multipart",
        )

    def test_upload_caption_and_private_authenticated_preview(self):
        added = self.upload()
        self.assertEqual(added.status_code, 201, added.data)
        self.assertEqual(added.data["caption"], "Brake pads replaced")
        self.assertTrue(added.data["hasUpload"])
        self.assertEqual(added.data["photo_type"], "work_update")
        self.assertEqual(added.data["url"], "")
        self.assertNotIn("file", added.data)

        stored = JobPhoto.objects.get(pk=added.data["id"])
        self.assertEqual(stored.job_id, Job.objects.get(pk=self.job_id).id)
        self.assertEqual(stored.company_id, self.company.id)
        self.assertEqual(stored.uploaded_by_id, self.user.id)
        self.assertTrue(stored.file.name.startswith("jobs/"))

        details = self.client.get(self.base)
        self.assertEqual(details.status_code, 200, details.data)
        self.assertTrue(any(p["hasUpload"] for p in details.data["photos"]))
        stream = self.client.get(f"{self.base}/photos/{stored.id}/file")
        self.assertEqual(stream.status_code, 200)
        self.assertIn("image/jpeg", stream["Content-Type"])
        self.assertEqual(stream["Cache-Control"], "private, no-store")
        self.assertTrue(b"".join(stream.streaming_content).startswith(b"\xff\xd8\xff"))

    def test_invalid_image_format_and_size_are_rejected(self):
        invalid = self.upload(self.image(b"NOT-JPEG" + b"x" * 20))
        self.assertEqual(invalid.status_code, 400)
        oversized = self.upload(self.image(b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024)))
        self.assertEqual(oversized.status_code, 400)
        self.assertEqual(JobPhoto.objects.count(), 0)

    def test_too_long_caption_is_rejected(self):
        result = self.upload(caption="X" * 256)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(JobPhoto.objects.count(), 0)

    def test_upload_requires_edit_permission_and_file_requires_view_permission(self):
        photo = self.upload()
        self.assertEqual(photo.status_code, 201)
        self.role.permissions = ["jobs.view"]
        self.role.save(update_fields=["permissions"])
        rejected = self.upload()
        self.assertEqual(rejected.status_code, 403)
        self.role.permissions = ["jobs.edit"]
        self.role.save(update_fields=["permissions"])
        rejected_read = self.client.get(f"{self.base}/photos/{photo.data['id']}/file")
        self.assertEqual(rejected_read.status_code, 403)

    def test_other_company_photos_are_not_accessible(self):
        photo = self.upload()
        self.assertEqual(photo.status_code, 201)
        company = Company.objects.create(name="Other Co", slug="other-photo-company")
        role = Role.objects.create(
            company=company, name="Other Manager", code="MANAGER",
            permissions=["jobs.view", "jobs.edit"],
        )
        user = User.objects.create(company=company, role=role, name="Other", email="other@photo.local")
        self.client.force_authenticate(user=user)
        for path in [self.base, f"{self.base}/photos/{photo.data['id']}/file"]:
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.upload().status_code, 404)

    def test_technician_notes_persist_across_job_gets(self):
        data = {"workUpdates": [{
            "id": "UPD-1", "type": "Work Update",
            "note": "Cleaned engine housing", "time": "11:00"
        }]}
        updated = self.client.patch(self.base, data, format="json")
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["workUpdates"][0]["note"], "Cleaned engine housing")
        after = self.client.get(self.base)
        self.assertEqual(after.status_code, 200, after.data)
        self.assertEqual(after.data["workUpdates"][0]["note"], "Cleaned engine housing")
