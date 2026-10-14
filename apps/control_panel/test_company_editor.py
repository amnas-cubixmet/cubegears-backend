"""Company create/edit: safe automatic slugs and image-upload UI with persistent file storage."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.accounts.models import User
from apps.companies.models import Company


def logo_file(filename="brand.png", image_format="PNG"):
    stream = BytesIO()
    Image.new("RGB", (32, 32), (90, 120, 210)).save(stream, format=image_format)
    return SimpleUploadedFile(filename, stream.getvalue(), content_type="image/png")


class CompanyEditorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="company-editor-test@example.com", name="Company Editor Admin",
            password="Company-editor-password-2026!",
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.media_dir = TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        media_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        media_override.enable()
        self.addCleanup(media_override.disable)

    @staticmethod
    def payload(name):
        return {
            "name": name,
            "country": "India",
            "currency": "INR",
            "plan": "starter",
            "is_active": "on",
        }

    def test_create_company_has_auto_slug_and_file_upload_ui(self):
        result = self.client.get(reverse("panel:company-create"))
        self.assertEqual(result.status_code, 200)
        self.assertTemplateUsed(result, "control_panel/company_form.html")
        self.assertContains(result, 'enctype="multipart/form-data"')
        self.assertContains(result, 'data-company-editor')
        self.assertContains(result, 'id="company-slug-preview"')
        self.assertContains(result, "Automatic")
        self.assertContains(result, 'name="logo_image"')
        self.assertNotContains(result, 'name="slug"')

    def test_slug_generated_and_duplicate_names_get_unique_suffixes(self):
        url = reverse("panel:company-create")
        payload = self.payload("Golden Wheels")
        payload["slug"] = "untrusted-client-value"
        self.assertRedirects(self.client.post(url, payload), reverse("panel:companies"))
        self.assertEqual(Company.objects.get().slug, "golden-wheels")
        self.assertRedirects(self.client.post(url, payload), reverse("panel:companies"))
        slugs = set(Company.objects.values_list("slug", flat=True))
        self.assertEqual(slugs, {"golden-wheels", "golden-wheels-2"})

    def test_edit_keeps_slug_when_company_name_changes(self):
        company = Company.objects.create(name="First Name", slug="permanent-company")
        data = self.payload("Second Name")
        data["slug"] = "bad-slug-override"
        url = reverse("panel:company-edit", args=[company.pk])
        self.assertRedirects(self.client.post(url, data), reverse("panel:companies"))
        company.refresh_from_db()
        self.assertEqual(company.name, "Second Name")
        self.assertEqual(company.slug, "permanent-company")
        response = self.client.get(url)
        self.assertContains(response, 'data-fixed-slug')

    def test_upload_stores_image_and_edit_keeps_existing_image(self):
        data = self.payload("Workshop With Image")
        data["logo_image"] = logo_file()
        response = self.client.post(reverse("panel:company-create"), data)
        self.assertRedirects(response, reverse("panel:companies"))
        company = Company.objects.get(slug="workshop-with-image")
        self.assertTrue(company.logo_image.name.startswith(f"company_logos/{company.pk}/"))
        self.assertTrue(Path(company.logo_image.path).is_file())
        self.assertTrue(company.logo_image.url.startswith("/media/company_logos/"))
        name = company.logo_image.name

        edit_url = reverse("panel:company-edit", args=[company.pk])
        response = self.client.get(edit_url)
        self.assertContains(response, "Current company logo")
        self.assertContains(response, company.logo_image.url)
        self.assertRedirects(
            self.client.post(edit_url, self.payload("Renamed Workshop")),
            reverse("panel:companies"),
        )
        company.refresh_from_db()
        self.assertEqual(company.logo_image.name, name)
        self.assertEqual(company.slug, "workshop-with-image")
        directory = self.client.get(reverse("panel:companies"))
        self.assertContains(directory, company.logo_image.url)
        detail = self.client.get(reverse("panel:company-detail", args=[company.pk]))
        self.assertContains(detail, company.logo_image.url)
        self.assertContains(detail, 'class="company-profile-header"')

    def test_wrong_extension_and_non_images_are_rejected(self):
        url = reverse("panel:company-create")
        bad = self.payload("Bad Content")
        bad["logo_image"] = SimpleUploadedFile(
            "logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            content_type="image/svg+xml",
        )
        response = self.client.post(url, bad)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload a valid image", status_code=200)
        self.assertFalse(Company.objects.filter(name="Bad Content").exists())

        mismatched = self.payload("Mismatched Image")
        mismatched["logo_image"] = logo_file(filename="logo.jpg")
        response = self.client.post(url, mismatched)
        self.assertContains(response, "File extension must match", status_code=200)
        self.assertFalse(Company.objects.filter(name="Mismatched Image").exists())

    def test_other_forms_and_authorization_remain_unchanged(self):
        self.assertTemplateUsed(
            self.client.get(reverse("panel:customer-create")), "control_panel/form.html",
        )
        self.client.logout()
        self.assertEqual(self.client.get(reverse("panel:company-create")).status_code, 302)
