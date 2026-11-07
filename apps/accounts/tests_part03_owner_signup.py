"""Part 03: owner password signup and refresh-token tenant access guards."""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User, StaffPasswordInvitation
from apps.companies.models import Company
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
    WHATSAPP_SEND_ENABLED=False,
)
class OwnerPasswordSignupTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_owner_creates_workshop_with_mobile_and_password_without_otp(self):
        response = self.client.post("/api/v1/auth/signup", {
            "workshop_name": "Direct Password Garage",
            "owner_name": "Workshop Owner",
            "mobile": "+919848484848",
            "password": "StrongDirectPass2026!",
            "confirm_password": "StrongDirectPass2026!",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["setupDelivery"], "password_created")
        self.assertNotIn("setupLink", response.data)
        self.assertNotIn("password", str(response.data))
        owner = User.objects.get(pk=response.data["user"]["id"])
        self.assertTrue(owner.check_password("StrongDirectPass2026!"))
        self.assertFalse(StaffPasswordInvitation.objects.filter(user=owner).exists())
        self.assertEqual(owner.company.owner_id, owner.id)
        logged = self.client.post("/api/v1/auth/login", {
            "identifier": "+919848484848", "password": "StrongDirectPass2026!",
        }, format="json")
        self.assertEqual(logged.status_code, 200, logged.data)
        self.assertIn("refresh", logged.data)

    def test_signup_password_confirmation_and_strength_enforced_before_writes(self):
        for password, confirm in [
            ("WeakPass2026!", "DifferentPass2026!"),
            ("12345678", "12345678"),
        ]:
            reply = self.client.post("/api/v1/auth/signup", {
                "workshop_name": "Invalid Garage",
                "owner_name": "Owner",
                "mobile": "+919848484849",
                "password": password,
                "confirm_password": confirm,
            }, format="json")
            self.assertEqual(reply.status_code, 400, reply.data)
        self.assertFalse(Company.objects.filter(name="Invalid Garage").exists())

    def test_refresh_refuses_disabled_role_and_company_without_new_access(self):
        response = self.client.post("/api/v1/auth/signup", {
            "workshop_name": "Revoked Login Garage",
            "owner_name": "Owner",
            "mobile": "+919848484850",
            "password": "StrongDirectPass2026!",
            "confirm_password": "StrongDirectPass2026!",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        login = self.client.post("/api/v1/auth/login", {
            "identifier": "+919848484850", "password": "StrongDirectPass2026!",
        }, format="json")
        self.assertEqual(login.status_code, 200, login.data)
        token = login.data["refresh"]
        self.assertEqual(self.client.post("/api/v1/auth/refresh", {"refresh": token}, format="json").status_code, 200)
        owner = User.objects.get(pk=response.data["user"]["id"])
        owner.role.is_active = False
        owner.role.save(update_fields=["is_active"])
        revoked = self.client.post("/api/v1/auth/refresh", {"refresh": token}, format="json")
        self.assertEqual(revoked.status_code, 401, revoked.data)

    def test_legacy_mobile_whatsapp_setup_still_supported(self):
        response = self.client.post("/api/v1/auth/signup", {
            "workshop_name": "Invitation Workshop",
            "owner_name": "Owner",
            "mobile": "+919848484851",
            "whatsapp_consent": True,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("setupLink", response.data)
        owner = User.objects.get(pk=response.data["user"]["id"])
        self.assertFalse(owner.has_usable_password())
        self.assertEqual(StaffPasswordInvitation.objects.filter(user=owner).count(), 1)
