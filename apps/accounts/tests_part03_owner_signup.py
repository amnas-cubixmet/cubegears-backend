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


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
    WHATSAPP_SEND_ENABLED=True,
    STAFF_WHATSAPP_AUTO_SEND_ENABLED=True,
    CELERY_BROKER_URL="redis://localhost:6379/0",
)
class AutomaticStaffWhatsAppTests(TestCase):
    """Automatic sends require explicit settings + consent + verified template."""

    def setUp(self):
        from apps.whatsapp.models import WhatsAppBusinessConnection, WhatsAppMessageTemplate

        self.company = Company.objects.create(
            name="Consent Workshop", slug="consent-workshop",
        )
        owner_role = Role.objects.create(
            company=self.company, name="Super Admin", code="SUPER_ADMIN",
            is_system=True, is_active=True, permissions=["*"],
        )
        self.owner = User.objects.create_user(
            email="owner@consent-workshop.test", name="Owner",
            phone="+919988001100", password="StrongOwnerPassword2026!",
            company=self.company, role=owner_role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.staff_role = Role.objects.create(
            company=self.company, name="Technician", code="TECHNICIAN",
            is_system=False, is_active=True, permissions=["jobs.view"],
        )
        WhatsAppBusinessConnection.objects.create(
            company=self.company, connected=True, notifications_enabled=True,
            messaging_mode="automatic", phone_number_id="official-phone",
            waba_id="official-waba", encrypted_access_token="test-cipher",
            event_settings={"staff_onboarding": True},
        )
        WhatsAppMessageTemplate.objects.create(
            company=self.company, event="staff_onboarding",
            template_name="staff_password_setup", approval_status="APPROVED",
        )
        self.admin = APIClient()
        self.admin.force_authenticate(self.owner)

    def register_staff(self, *, consent=True):
        return self.admin.post("/api/v1/employees", {
            "name": "New Technician", "phone": "+919988001101",
            "roleId": str(self.staff_role.pk), "role": "Technician",
            "employmentStatus": "Active", "loginAccessEnabled": True,
            "whatsappConsent": consent,
            "whatsappConsentSource": "Signed onboarding consent" if consent else "",
        }, format="json")

    def test_opted_in_staff_queues_and_sends_one_official_template(self):
        from unittest.mock import patch
        from apps.whatsapp.staff_invites import send_staff_invitation

        with patch("apps.whatsapp.staff_invites.send_staff_invitation.delay") as task:
            with self.captureOnCommitCallbacks(execute=True):
                result = self.register_staff()
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data["whatsappInviteStatus"], "queued")
        task.assert_called_once()
        invite = StaffPasswordInvitation.objects.get(user_id=result.data["user"])
        self.assertEqual(invite.delivery_status, "queued")
        with patch("apps.whatsapp.staff_invites.decrypt_token", return_value="mock-server-secret"), patch(
            "apps.whatsapp.staff_invites.send_template_message",
            return_value={"messages": [{"id": "wamid.test.123"}]},
        ) as sender:
            self.assertEqual(send_staff_invitation.run(str(invite.pk)), "sent")
            self.assertEqual(send_staff_invitation.run(str(invite.pk)), "sent")
        sender.assert_called_once()
        invite.refresh_from_db()
        self.assertEqual(invite.delivery_status, "sent")
        self.assertEqual(invite.meta_message_id, "wamid.test.123")
        self.assertEqual(invite.attempts, 1)
        self.assertNotIn("mock-server-secret", str(result.data))

    def test_no_staff_consent_falls_back_to_manual_and_never_queues(self):
        from unittest.mock import patch

        with patch("apps.whatsapp.staff_invites.send_staff_invitation.delay") as task:
            with self.captureOnCommitCallbacks(execute=True):
                reply = self.register_staff(consent=False)
        self.assertEqual(reply.status_code, 201, reply.data)
        self.assertEqual(reply.data["whatsappInviteStatus"], "manual")
        self.assertIn("/set-password/", reply.data["setupLink"])
        task.assert_not_called()
