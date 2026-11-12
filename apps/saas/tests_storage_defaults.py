"""Tenant storage defaults, safe upload pause and byte-accurate usage."""
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.models import MediaFile


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class StorageDefaultAndUnitsTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cubixgear-test-storage-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.media_override = override_settings(MEDIA_ROOT=self.tmp)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)

        self.company = Company.objects.create(
            name="Storage Demo", slug="storage-default-on",
            require_paid_features=False,
        )
        role = Role.objects.create(
            company=self.company, name="Owner", code="SUPER_ADMIN",
            permissions=["*"], is_active=True,
        )
        self.owner = User.objects.create_user(
            email="storage-owner@example.test", password="StrongTestPass2026!",
            name="Owner", company=self.company, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def file(self):
        return SimpleUploadedFile("small.png", b"x" * 1024, content_type="image/png")

    def test_storage_is_enabled_by_default_without_a_database_setting(self):
        summary = self.client.get("/api/v1/saas/storage")
        self.assertEqual(summary.status_code, 200, summary.data)
        self.assertIs(summary.data["settings"]["enabled"], True)
        self.assertEqual(summary.data["usedBytes"], 0)
        self.assertEqual(summary.data["fileCount"], 0)

    def test_1024_bytes_display_without_rounded_gigabyte_loss(self):
        uploaded = self.client.post(
            "/api/v1/saas/storage/files",
            {"files": [self.file()], "category": "General"}, format="multipart",
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.data)
        self.assertEqual(uploaded.data[0]["sizeBytes"], 1024)
        summary = self.client.get("/api/v1/saas/storage")
        self.assertEqual(summary.data["usedBytes"], 1024)
        self.assertEqual(summary.data["fileCount"], 1)
        self.assertTrue(summary.data["usedGb"] < 0.001)

    def test_disabling_storage_blocks_new_files_but_preserves_existing_files(self):
        uploaded = self.client.post(
            "/api/v1/saas/storage/files",
            {"files": [self.file()], "category": "General"}, format="multipart",
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.data)
        item_id = uploaded.data[0]["id"]

        invalid = self.client.put(
            "/api/v1/saas/storage/settings", {"enabled": "false"}, format="json",
        )
        self.assertEqual(invalid.status_code, 400, invalid.data)

        changed = self.client.put(
            "/api/v1/saas/storage/settings", {"enabled": False}, format="json",
        )
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertIs(changed.data["settings"]["enabled"], False)
        self.assertEqual(changed.data["usedBytes"], 1024)

        blocked = self.client.post(
            "/api/v1/saas/storage/files",
            {"files": [self.file()], "category": "General"}, format="multipart",
        )
        self.assertEqual(blocked.status_code, 403, blocked.data)
        blocked_replace = self.client.patch(
            f"/api/v1/saas/storage/files/{item_id}",
            {"file": self.file()}, format="multipart",
        )
        self.assertEqual(blocked_replace.status_code, 403, blocked_replace.data)
        self.assertEqual(MediaFile.objects.filter(company=self.company).count(), 1)

        restored = self.client.put(
            "/api/v1/saas/storage/settings", {"enabled": True}, format="json",
        )
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertIs(restored.data["settings"]["enabled"], True)
