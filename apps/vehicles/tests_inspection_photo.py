"""Private images must never leak across tenants or prior to publication."""
from io import BytesIO
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.customers.portal_tokens import ensure_portal_access, issue_link
from apps.roles.models import Role
from apps.vehicles.models import Vehicle, VehicleInspectionPhoto


def sample_photo():
    data = BytesIO()
    Image.new("RGB", (12, 7), (44, 150, 210)).save(data, format="PNG")
    return SimpleUploadedFile("inspection.png", data.getvalue(), content_type="image/png")


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
    CUSTOMER_PORTAL_BASE_URL="https://customer.cubegears.com/c",
)
class PrivateVehiclePhotoTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Photo Workshop A", slug="photo-ws-a")
        self.other = Company.objects.create(name="Photo Workshop B", slug="photo-ws-b")
        self.role = Role.objects.create(company=self.company, code="PHOTO_OWNER", name="Owner",
                                        permissions=["vehicles.view","vehicles.create","vehicles.edit"],is_active=True)
        self.owner = User.objects.create_user(
            email="photo-owner@example.test", password="safe-pass", name="Photo Owner",
            company=self.company, role=self.role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(company=self.company,name="Customer A", phone="+919888888888")
        self.other_customer = Customer.objects.create(company=self.other,name="Customer B", phone="+919777777777")
        self.vehicle = Vehicle.objects.create(company=self.company,customer=self.customer,registration="KL10XY1000")
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.public = APIClient()

    def test_photo_is_private_by_default_then_available_only_to_matching_customer(self):
        with TemporaryDirectory() as root, override_settings(PRIVATE_JOB_MEDIA_ROOT=root):
            reply = self.client.post(f"/api/v1/vehicles/{self.vehicle.pk}/photos", {
                "image": sample_photo(), "category": "front", "phase": "before",
                "caption": "Front bumper",
            }, format="multipart")
            self.assertEqual(reply.status_code, 201, getattr(reply,"data",None))
            photo = VehicleInspectionPhoto.objects.get(pk=reply.data["id"])
            self.assertFalse(photo.published)
            self.assertEqual(photo.category, "front")
            self.assertTrue(photo.file.name.endswith(".jpg"))

            token = issue_link(ensure_portal_access(self.customer)).rsplit("/",1)[-1]
            public_photo = f"/api/v1/c/{token}/photos/{photo.id}"
            self.assertEqual(self.public.get(public_photo).status_code, 404)
            snapshot = self.public.get(f"/api/v1/c/{token}")
            self.assertEqual(snapshot.status_code, 200)
            self.assertEqual(snapshot.data["photos"], [])

            update = self.client.patch(
                f"/api/v1/vehicle-photos/{photo.pk}", {"published":True}, format="json",
            )
            self.assertEqual(update.status_code, 200, update.data)
            published = self.public.get(public_photo)
            self.assertEqual(published.status_code, 200)
            self.assertEqual(published["Content-Type"], "image/jpeg")
            self.assertTrue(b"".join(published.streaming_content).startswith(b"\xff\xd8"))
            snapshot = self.public.get(f"/api/v1/c/{token}")
            self.assertEqual(len(snapshot.data["photos"]), 1)
            other_token = issue_link(ensure_portal_access(self.other_customer)).rsplit("/", 1)[-1]
            self.assertEqual(self.public.get(f"/api/v1/c/{other_token}/photos/{photo.pk}").status_code, 404)

    def test_workshop_may_explicitly_publish_new_photo_on_upload(self):
        with TemporaryDirectory() as root, override_settings(PRIVATE_JOB_MEDIA_ROOT=root):
            reply = self.client.post(f"/api/v1/vehicles/{self.vehicle.pk}/photos", {
                "image": sample_photo(), "category": "damage", "phase": "before",
                "caption": "Published during inspection", "published": "true",
            }, format="multipart")
            self.assertEqual(reply.status_code, 201, getattr(reply, "data", None))
            self.assertTrue(reply.data["published"])
            photo = VehicleInspectionPhoto.objects.get(pk=reply.data["id"])
            self.assertTrue(photo.published)
            token = issue_link(ensure_portal_access(self.customer)).rsplit("/", 1)[-1]
            image = self.public.get(f"/api/v1/c/{token}/photos/{photo.pk}")
            self.assertEqual(image.status_code, 200)
            self.assertEqual(image["Content-Type"], "image/jpeg")
            other_token = issue_link(ensure_portal_access(self.other_customer)).rsplit("/", 1)[-1]
            self.assertEqual(
                self.public.get(f"/api/v1/c/{other_token}/photos/{photo.pk}").status_code,
                404,
            )
            self.assertEqual(
                len(self.public.get(f"/api/v1/c/{token}").data["photos"]), 1,
            )

    def test_create_only_staff_cannot_publish_during_upload(self):
        role = Role.objects.create(
            company=self.company, code="PHOTO_CREATE_ONLY", name="Create Only",
            permissions=["vehicles.create"], is_active=True,
        )
        creator = User.objects.create_user(
            email="photo-creator@example.test", password="safe-pass",
            name="Photo Creator", company=self.company, role=role,
        )
        client = APIClient()
        client.force_authenticate(user=creator)
        with TemporaryDirectory() as root, override_settings(PRIVATE_JOB_MEDIA_ROOT=root):
            published = client.post(f"/api/v1/vehicles/{self.vehicle.pk}/photos", {
                "image": sample_photo(), "published": "true",
            }, format="multipart")
            self.assertEqual(published.status_code, 403)
            self.assertFalse(VehicleInspectionPhoto.objects.exists())
            private = client.post(f"/api/v1/vehicles/{self.vehicle.pk}/photos", {
                "image": sample_photo(),
            }, format="multipart")
            self.assertEqual(private.status_code, 201, getattr(private, "data", None))
            self.assertFalse(VehicleInspectionPhoto.objects.get(pk=private.data["id"]).published)

    def test_upload_rejects_non_boolean_visibility(self):
        with TemporaryDirectory() as root, override_settings(PRIVATE_JOB_MEDIA_ROOT=root):
            reply = self.client.post(f"/api/v1/vehicles/{self.vehicle.pk}/photos", {
                "image": sample_photo(), "published": "yes",
            }, format="multipart")
            self.assertEqual(reply.status_code, 400)
            self.assertFalse(VehicleInspectionPhoto.objects.exists())

    def test_upload_validation_and_public_metadata_are_restricted(self):
        bad = SimpleUploadedFile("text.png", b"this is not an image", content_type="image/png")
        reply = self.client.post(
            f"/api/v1/vehicles/{self.vehicle.pk}/photos", {"image":bad}, format="multipart",
        )
        self.assertEqual(reply.status_code, 400)
        self.assertFalse(VehicleInspectionPhoto.objects.exists())
        self.assertEqual(self.public.get("/api/v1/vehicle-photos/00000000-0000-0000-0000-000000000001/file").status_code in {401,403}, True)

    def test_other_company_photo_collection_not_visible_to_staff(self):
        other_vehicle = Vehicle.objects.create(
            company=self.other,customer=self.other_customer, registration="KL10ZZ1099",
        )
        self.assertEqual(
            self.client.get(f"/api/v1/vehicles/{other_vehicle.id}/photos").status_code, 404,
        )
