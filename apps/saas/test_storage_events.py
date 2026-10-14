import tempfile
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role
from .models import StorageEvent, MediaFile


class StorageEventTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        media = override_settings(MEDIA_ROOT=temp.name)
        media.enable()
        self.addCleanup(media.disable)
        self.company = Company.objects.create(name="Store A", slug="store-a")
        self.other = Company.objects.create(name="Store B", slug="store-b")
        role = Role.objects.create(company=self.company, name="Manager", code="ADMIN",
                                   permissions=["storage.view", "storage.manage"])
        self.user = User.objects.create_user(email="ledger@example.test", password="SafePassword123!",
                                             name="Storage Manager", company=self.company, role=role)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def upload(self, name, data):
        return self.client.post("/api/v1/saas/storage/files",
                                {"files": [SimpleUploadedFile(name, data, content_type="image/png")]},
                                format="multipart")

    def test_log_upload_delete_and_tenant_separation(self):
        result = self.upload("a.png", b"012345")
        self.assertEqual(result.status_code, 201)
        file_id = result.data[0]["id"]
        event = StorageEvent.objects.get(event_type="UPLOAD")
        self.assertEqual(event.delta_bytes, 6)
        self.assertEqual(event.storage_before_bytes, 0)
        self.assertEqual(event.storage_after_bytes, 6)
        self.assertEqual(self.client.get("/api/v1/saas/storage/events").data["count"], 1)
        self.assertEqual(self.client.get(f"/api/v1/saas/storage/files/{file_id}/download").status_code, 200)
        removed = self.client.post("/api/v1/saas/storage/files/delete", {"ids": [str(file_id)]}, format="json")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.data["deleted"], 1)
        self.assertEqual(MediaFile.objects.filter(company=self.company).count(), 0)
        deletion = StorageEvent.objects.get(event_type="DELETE")
        self.assertEqual(deletion.delta_bytes, -6)
        self.assertEqual(deletion.storage_after_bytes, 0)
        self.assertEqual(str(deletion.file_id_snapshot), str(file_id))
        from django.utils import timezone
        today=timezone.localdate().isoformat()
        details=self.client.get(f"/api/v1/saas/storage/history/{today}")
        self.assertEqual(details.status_code, 200)
        self.assertEqual(details.data["files"], [])
        self.assertEqual(len(details.data["events"]), 2)
        self.assertEqual(self.client.post("/api/v1/saas/storage/events").status_code, 405)
        self.assertEqual(self.client.get("/api/v1/saas/storage/events?limit=101").status_code, 400)
        other_role = Role.objects.create(company=self.other, name="Manager", code="ADMIN",
                                          permissions=["storage.view"])
        other_user = User.objects.create_user(email="other-ledger@example.test", password="SafePassword123!",
                                              name="Other", company=self.other, role=other_role)
        self.client.force_authenticate(other_user)
        self.assertEqual(self.client.get("/api/v1/saas/storage/events").data["count"], 0)
        self.assertEqual(self.client.get(f"/api/v1/saas/storage/files/{file_id}/download").status_code, 404)

    def test_preexisting_media_is_recorded_once_as_baseline(self):
        MediaFile.objects.create(company=self.company,
                                 file=SimpleUploadedFile("old.png", b"12", content_type="image/png"),
                                 name="old.png", size_bytes=2)
        self.assertEqual(self.upload("new.png", b"123").status_code, 201)
        self.assertEqual(StorageEvent.objects.filter(event_type="BASELINE").count(), 1)
        new = StorageEvent.objects.get(event_type="UPLOAD")
        self.assertEqual(new.storage_before_bytes, 2)
        self.assertEqual(new.storage_after_bytes, 5)
        self.assertEqual(self.upload("newer.png", b"4").status_code, 201)
        self.assertEqual(StorageEvent.objects.filter(event_type="BASELINE").count(), 1)
