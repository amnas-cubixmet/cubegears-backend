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
        self.assertNotIn("StrongDirectPass2026!", str(response.data))
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

    def test_phone_only_owner_registration_does_not_expose_admin_bearer_link(self):
        response = self.client.post("/api/v1/auth/signup", {
            "workshop_name": "Invitation Workshop",
            "owner_name": "Owner",
            "mobile": "+919848484851",
            "whatsapp_consent": True,
        }, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("email", response.data)
        self.assertFalse(Company.objects.filter(name="Invitation Workshop").exists())



@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
    STAFF_WHATSAPP_AUTO_SEND_ENABLED=True,
    WHATSAPP_SEND_ENABLED=True,
    CELERY_BROKER_URL="redis://localhost:6379/0",
)
class RetiredAutomaticWhatsAppTests(TestCase):
    """Legacy settings cannot trigger Cloud API delivery after migration."""

    def setUp(self):
        self.company = Company.objects.create(name="Manual Staff", slug="manual-staff")
        owner_role = Role.objects.create(
            company=self.company, name="Super Admin", code="SUPER_ADMIN",
            is_system=True, is_active=True, permissions=["*"],
        )
        self.owner = User.objects.create_user(
            email="owner@manual-staff.test", name="Owner",
            phone="+919988001100", password="StrongOwnerPassword2026!",
            company=self.company, role=owner_role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.staff_role = Role.objects.create(
            company=self.company, name="Technician", code="TECHNICIAN",
            is_system=False, is_active=True, permissions=["jobs.view"],
        )
        from apps.whatsapp.models import WhatsAppBusinessConnection, WhatsAppMessageTemplate
        WhatsAppBusinessConnection.objects.create(
            company=self.company, connected=True, notifications_enabled=True,
            messaging_mode="automatic", phone_number_id="legacy-phone",
            waba_id="legacy-waba", encrypted_access_token="old-token",
            event_settings={"staff_onboarding": True},
        )
        WhatsAppMessageTemplate.objects.create(
            company=self.company, event="staff_onboarding",
            template_name="legacy_staff_template", approval_status="APPROVED",
        )
        self.admin = APIClient()
        self.admin.force_authenticate(user=self.owner)

    def test_staff_invitation_remains_manual_even_with_legacy_automatic_settings(self):
        from unittest.mock import patch
        from apps.whatsapp.staff_invites import send_staff_invitation
        with patch("apps.whatsapp.staff_invites.send_template_message") as sender:
            created = self.admin.post("/api/v1/employees", {
                "name": "New Technician", "phone": "+919988001101",
                "roleId": str(self.staff_role.pk), "role": "Technician",
                "employmentStatus": "Active", "loginAccessEnabled": True,
                "whatsappConsent": True, "whatsappConsentSource": "Signed onboarding consent",
            }, format="json")
            self.assertEqual(created.status_code, 201, created.data)
            self.assertEqual(created.data["whatsappInviteStatus"], "manual")
            self.assertIn("/set-password/", created.data["setupLink"])
            invite = StaffPasswordInvitation.objects.get(user_id=created.data["user"])
            self.assertEqual(send_staff_invitation.run(str(invite.pk)), "manual_only")
            sender.assert_not_called()
        self.assertEqual(invite.delivery_status, "manual")
