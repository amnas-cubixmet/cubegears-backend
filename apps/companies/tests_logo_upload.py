"""Company logo upload and preview use the signed-in tenant, not settings JSON."""
from io import BytesIO
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role


def make_logo():
    output = BytesIO()
    Image.new("RGB", (5, 5), (15, 100, 180)).save(output, format="PNG")
    return SimpleUploadedFile("workshop-logo.png", output.getvalue(), content_type="image/png")


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class CompanyLogoApiTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Logo Workshop", slug="logo-workshop-test")
        role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER_LOGO_TEST",
            permissions=["settings.view", "settings.manage", "company.manage"], is_active=True,
        )
        self.owner = User.objects.create_user(
            email="logo-owner@example.test", password="logo-test-secret",
            name="Logo Owner", company=self.company, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_logo_upload_and_authenticated_preview(self):
        with TemporaryDirectory() as storage:
            with override_settings(MEDIA_ROOT=storage):
                upload = self.client.patch(
                    "/api/v1/company/me", {"logo_image": make_logo()}, format="multipart",
                )
                self.assertEqual(upload.status_code, 200, upload.data)
                self.company.refresh_from_db()
                self.assertTrue(self.company.logo_image.name.startswith(f"company_logos/{self.company.pk}/"))
                preview = self.client.get("/api/v1/company/logo")
                self.assertEqual(preview.status_code, 200)
                self.assertEqual(preview["Content-Type"], "image/png")
                self.assertIn("private", preview["Cache-Control"])
                self.assertTrue(b"".join(preview.streaming_content).startswith(b"\x89PNG"))

    def test_preview_needs_authentication_and_own_company_image(self):
        self.assertEqual(self.client.get("/api/v1/company/logo").status_code, 404)
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get("/api/v1/company/logo").status_code, [401, 403])

    def test_read_only_logo_echo_does_not_break_company_settings_or_override_logo(self):
        self.company.logo = "https://example.test/original.png"
        self.company.save(update_fields=["logo"])
        updated = self.client.put(
            "/api/v1/settings/company",
            {"name": "Renamed Workshop", "logo": "https://evil.example/logo.png"},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Renamed Workshop")
        self.assertEqual(self.company.logo, "https://example.test/original.png")
