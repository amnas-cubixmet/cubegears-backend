"""Security regression for login-less, per-customer tracking capabilities."""
from tempfile import TemporaryDirectory
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer, CustomerPortalAccess
from apps.customers.portal_tokens import ensure_portal_access, issue_link, resolve_link
from apps.jobs.models import Job
from apps.roles.models import Role
from apps.vehicles.models import Vehicle


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
    CUSTOMER_PORTAL_BASE_URL="https://customer.cubegears.com/c",
)
class CustomerPortalSecurityTests(TestCase):
    def setUp(self):
        self.workshop = Company.objects.create(name="Portal Workshop A", slug="portal-ws-a")
        self.other_workshop = Company.objects.create(name="Portal Workshop B", slug="portal-ws-b")
        role = Role.objects.create(
            company=self.workshop, name="Owner", code="OWNER_PORTAL_SECURITY",
            permissions=["customers.view","customers.edit"], is_active=True,
        )
        self.owner = User.objects.create_user(
            email="portal-owner@example.test", password="safe-test-password",
            name="Workshop Owner", company=self.workshop, role=role,
        )
        self.workshop.owner = self.owner
        self.workshop.save(update_fields=["owner"])
        self.customer = Customer.objects.create(
            company=self.workshop, name="Owner Customer", phone="+919999999999",
            email="sensitive@example.test", notes="PRIVATE WORKSHOP NOTES",
        )
        self.stranger = Customer.objects.create(
            company=self.other_workshop, name="Other Customer", phone="+919888888888",
        )
        self.vehicle = Vehicle.objects.create(
            company=self.workshop, customer=self.customer,
            registration="KL10AB1234", make="Toyota", model="Innova",
        )
        Job.objects.create(
            company=self.workshop, customer=self.customer, vehicle=self.vehicle,
            job_number="JC-1025", status="Inspection",
            notes="PRIVATE PURCHASE COST", complaints=[{"description":"Sensitive details"}],
        )
        self.staff = APIClient()
        self.staff.force_authenticate(user=self.owner)
        self.public = APIClient()

    def token(self, record):
        return issue_link(record).rsplit("/", 1)[-1]

    def test_customer_creation_automatically_creates_one_stable_secure_token(self):
        self.assertEqual(CustomerPortalAccess.objects.filter(customer=self.customer).count(), 1)
        record = ensure_portal_access(self.customer)
        first = issue_link(record)
        self.assertEqual(first, issue_link(ensure_portal_access(self.customer)))
        self.assertNotIn(str(self.customer.id), first)
        self.assertEqual(len(record.token_hash), 64)
        self.assertNotIn(self.token(record), record.token_hash)

    def test_public_response_uses_allowlist_never_private_customer_or_job_fields(self):
        record = ensure_portal_access(self.customer)
        reply = self.public.get("/api/v1/c/" + self.token(record))
        self.assertEqual(reply.status_code, 200, getattr(reply, "data", None))
        self.assertEqual(reply.data["vehicles"][0]["registration"], "KL10AB1234")
        self.assertEqual(reply.data["jobs"][0]["number"], "JC-1025")
        body = str(reply.data)
        self.assertNotIn("sensitive@example.test", body)
        self.assertNotIn("PRIVATE WORKSHOP NOTES", body)
        self.assertNotIn("PRIVATE PURCHASE COST", body)
        self.assertNotIn(self.stranger.name, body)
        self.assertNotIn("Sensitive details", body)
        self.assertNotIn("password", body.lower())

    def test_revocation_and_rotation_immediately_invalidate_previous_link(self):
        old = self.token(ensure_portal_access(self.customer))
        revoke = self.staff.post(
            f"/api/v1/customer-portal/links/{self.customer.id}", {"action":"revoke"}, format="json",
        )
        self.assertEqual(revoke.status_code, 200)
        self.assertEqual(self.public.get("/api/v1/c/" + old).status_code, 404)
        rotate = self.staff.post(
            f"/api/v1/customer-portal/links/{self.customer.id}", {"action":"rotate"}, format="json",
        )
        self.assertEqual(rotate.status_code, 200, rotate.data)
        new = rotate.data["url"].rsplit("/", 1)[-1]
        self.assertNotEqual(new, old)
        self.assertEqual(self.public.get("/api/v1/c/" + old).status_code, 404)
        self.assertEqual(self.public.get("/api/v1/c/" + new).status_code, 200)
        self.assertIsNone(resolve_link(old))

    def test_cross_company_link_access_is_rejected_for_staff(self):
        foreign = self.staff.get(f"/api/v1/customer-portal/links/{self.stranger.pk}")
        self.assertEqual(foreign.status_code, 404)
        foreign_rotate = self.staff.post(
            f"/api/v1/customer-portal/links/{self.stranger.pk}", {"action":"rotate"}, format="json",
        )
        self.assertEqual(foreign_rotate.status_code, 404)

    def test_malformed_token_and_revoked_access_do_not_leak_customer_data(self):
        self.assertEqual(self.public.get("/api/v1/c/not-a-secret").status_code, 404)
        access = ensure_portal_access(self.customer)
        original = self.token(access)
        self.assertEqual(self.public.get("/api/v1/c/" + original[:-1] + ("0" if original[-1] != "0" else "1")).status_code, 404)

    def test_logo_stream_is_scoped_to_signed_active_customer_portal_link(self):
        content = b"test-private-logo-file"
        with TemporaryDirectory() as root, override_settings(MEDIA_ROOT=root):
            self.workshop.logo_image.save("company.png", ContentFile(content), save=True)
            token = self.token(ensure_portal_access(self.customer))
            detail = self.public.get("/api/v1/c/" + token)
            self.assertTrue(detail.data["workshop"]["logoAvailable"])
            self.assertEqual(detail.data["workshop"]["name"], self.workshop.name)

            response = self.public.get("/api/v1/c/" + token + "/logo")
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response["Cache-Control"])
            self.assertEqual(b"".join(response.streaming_content), content)
            self.assertNotIn("logo_image", str(detail.data))

            other = self.token(ensure_portal_access(self.stranger))
            self.assertEqual(self.public.get("/api/v1/c/" + other + "/logo").status_code, 404)
            self.staff.post(f"/api/v1/customer-portal/links/{self.customer.id}",
                            {"action": "revoke"}, format="json")
            self.assertEqual(self.public.get("/api/v1/c/" + token + "/logo").status_code, 404)
