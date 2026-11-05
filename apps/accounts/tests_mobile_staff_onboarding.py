"""Regression: OTP-free workshop/mobile login and one-use staff password invitations."""
from datetime import timedelta
from django.test import TestCase, override_settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User, StaffPasswordInvitation
from apps.accounts.phone import normalize_phone
from apps.companies.models import Company
from apps.employees.models import Employee
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
    WHATSAPP_SEND_ENABLED=False,
    EMAIL_CELERY_ENABLED=False,
)
class MobileStaffOnboardingTests(TestCase):
    def setUp(self):
        # DRF anonymous login/signup throttles share cache across test cases.
        cache.clear()
        self.company = Company.objects.create(name="Phone Service Workshop", slug="phone-workshop")
        self.owner_role = Role.objects.create(
            company=self.company, name="Super Admin", code="SUPER_ADMIN",
            permissions=["*"], is_system=True, is_active=True,
        )
        self.owner = User.objects.create_user(
            email="owner@phone-workshop.test", password="Owner-Pass12345!",
            name="Owner", phone="+919222222222", company=self.company,
            role=self.owner_role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.staff_role = Role.objects.create(
            company=self.company, name="Mechanic", code="MECHANIC_PHONE",
            permissions=["dashboard.view","jobs.view"],is_system=False,is_active=True,
        )
        self.admin = APIClient()
        self.admin.force_authenticate(user=self.owner)
        self.public = APIClient()

    def create_staff(self, phone="+919333333333", email="", consent=False):
        result = self.admin.post("/api/v1/employees", {
            "name": "New Mechanic", "phone": phone, "email":email,
            "roleId": str(self.staff_role.pk), "role":"Mechanic",
            "loginAccessEnabled":True, "employmentStatus":"Active",
            "whatsappConsent":consent,
            "whatsappConsentSource":"Signed staff consent form" if consent else "",
        }, format="json")
        self.assertEqual(result.status_code, 201, getattr(result,"data",None))
        return result

    def test_phone_format_is_international_and_does_not_infer_country(self):
        self.assertEqual(normalize_phone(" +91 (93333) 33333 "),"+919333333333")
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            normalize_phone("9333333333")

    def test_staff_creates_login_with_only_mobile_and_private_one_use_link(self):
        reply = self.create_staff()
        employee = Employee.objects.get(pk=reply.data["id"])
        self.assertIsNotNone(employee.user_id)
        self.assertIsNone(employee.user.email)
        self.assertEqual(employee.user.normalized_phone, "+919333333333")
        self.assertFalse(employee.user.has_usable_password())
        self.assertEqual(reply.data["loginStatus"],"Pending Password Setup")
        self.assertEqual(reply.data["whatsappInviteStatus"],"manual")
        self.assertIn("/set-password/",reply.data["setupLink"])
        self.assertNotIn(reply.data["setupLink"], str(employee.user.staff_password_invitations.first().token_hash))
        before = self.public.post("/api/v1/auth/login", {
            "identifier":"+919333333333","password":"StrongPass23!"
        },format="json")
        self.assertEqual(before.status_code,400)
        token = reply.data["setupLink"].rsplit("/",1)[-1]
        valid = self.public.post("/api/v1/auth/staff-invitation/validate",{"token":token},format="json")
        self.assertEqual(valid.status_code,200,valid.data)
        consumed = self.public.post("/api/v1/auth/staff-invitation/consume", {
            "token":token, "password":"StrongPass23!","confirmPassword":"StrongPass23!",
        },format="json")
        self.assertEqual(consumed.status_code,200,consumed.data)
        again = self.public.post("/api/v1/auth/staff-invitation/consume", {
            "token":token, "password":"StrongPass23!","confirmPassword":"StrongPass23!",
        },format="json")
        self.assertEqual(again.status_code,400)
        signed_in = self.public.post("/api/v1/auth/login", {
            "identifier":"+919333333333","password":"StrongPass23!",
        },format="json")
        self.assertEqual(signed_in.status_code,200,signed_in.data)
        self.assertEqual(signed_in.data["user"]["id"],str(employee.user_id))
        self.assertFalse(signed_in.data["user"]["is_superuser"])
        self.assertIn("jobs.view",signed_in.data["user"]["effectivePermissions"])

    def test_resend_invalidates_old_link_and_expired_is_rejected(self):
        first = self.create_staff()
        employee = Employee.objects.get(pk=first.data["id"])
        old = first.data["setupLink"].rsplit("/",1)[-1]
        # One-minute repeat submissions are idempotent.
        repeat = self.admin.post(f"/api/v1/employees/{employee.pk}/invite",{},format="json")
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(repeat.data["setupLink"], first.data["setupLink"])
        self.assertEqual(StaffPasswordInvitation.objects.filter(user=employee.user).count(), 1)
        # Once cooldown passes, an explicit resend rotates and revokes the old token.
        current = StaffPasswordInvitation.objects.get(user=employee.user)
        StaffPasswordInvitation.objects.filter(pk=current.pk).update(
            created_at=timezone.now() - timedelta(seconds=70),
        )
        new = self.admin.post(f"/api/v1/employees/{employee.pk}/invite",{},format="json")
        self.assertEqual(new.status_code,200,new.data)
        self.assertNotEqual(new.data["setupLink"],first.data["setupLink"])
        self.assertEqual(self.public.post("/api/v1/auth/staff-invitation/validate",{"token":old},format="json").status_code,400)
        record = StaffPasswordInvitation.objects.filter(user=employee.user).latest("created_at")
        record.expires_at = timezone.now() - timedelta(seconds=1)
        record.save(update_fields=["expires_at"])
        self.assertEqual(self.public.post("/api/v1/auth/staff-invitation/validate", {
            "token":new.data["setupLink"].rsplit("/",1)[-1],
        },format="json").status_code,400)

    def test_disable_login_does_not_disable_employment_or_payroll(self):
        reply = self.create_staff()
        employee = Employee.objects.get(pk=reply.data["id"])
        action = self.admin.patch(f"/api/v1/employees/{employee.pk}/login-access", {
            "enabled":False,
        },format="json")
        self.assertEqual(action.status_code,200,action.data)
        employee.refresh_from_db()
        self.assertEqual(employee.status,"Active")
        self.assertFalse(employee.user.is_active)
        self.assertEqual(action.data["loginStatus"],"Disabled")
        self.assertEqual(self.public.post("/api/v1/auth/staff-invitation/validate", {
            "token":reply.data["setupLink"].rsplit("/",1)[-1],
        },format="json").status_code,400)

    def test_shared_mobile_across_workshops_requires_explicit_workshop_code(self):
        other = Company.objects.create(name="Other Workshop",slug="another-phone-workshop")
        role = Role.objects.create(
            company=other,name="Super Admin",code="SUPER_ADMIN",
            permissions=["*"],is_system=True,is_active=True,
        )
        other_owner = User.objects.create_user(
            email=None,phone="+919222222222",password="Owner-Pass12345!",
            name="Other Owner",company=other,role=role,
        )
        other.owner = other_owner
        other.save(update_fields=["owner"])
        ambiguous = self.public.post("/api/v1/auth/login", {
            "identifier":"+919222222222","password":"Owner-Pass12345!",
        },format="json")
        self.assertEqual(ambiguous.status_code,400)
        self.assertIn("workshop",ambiguous.data)
        resolved = self.public.post("/api/v1/auth/login", {
            "identifier":"+919222222222","password":"Owner-Pass12345!","workshop":"phone-workshop",
        },format="json")
        self.assertEqual(resolved.status_code,200,resolved.data)
        self.assertEqual(resolved.data["user"]["id"],str(self.owner.pk))

    def test_email_and_mobile_platform_admin_still_work(self):
        platform = User.objects.create_superuser(
            email="platform@example.test",password="PlatformPass123!",
            name="Platform Admin",phone="+971501112233",
        )
        for identifier in ["platform@example.test", "+971501112233"]:
            response = self.public.post("/api/v1/auth/login", {
                "identifier":identifier,"password":"PlatformPass123!",
            },format="json")
            self.assertEqual(response.status_code,200,response.data)
            self.assertEqual(response.data["user"]["id"],str(platform.pk))

    def test_staff_role_is_not_admin_and_other_tenant_cannot_disable_login(self):
        created = self.create_staff()
        staff_id = created.data["id"]
        second = Company.objects.create(name="Different Workshop",slug="different-workshop")
        second_role = Role.objects.create(
            company=second,name="Super Admin",code="SUPER_ADMIN",
            permissions=["*"],is_system=True,is_active=True,
        )
        second_owner = User.objects.create_user(
            email="other-owner@example.test",phone="+971522221111",
            password="OtherPass12345!",name="Other Owner",
            company=second,role=second_role,
        )
        second.owner = second_owner
        second.save(update_fields=["owner"])
        intruder = APIClient()
        intruder.force_authenticate(user=second_owner)
        reply = intruder.patch(f"/api/v1/employees/{staff_id}/login-access",{
            "enabled":False,
        },format="json")
        self.assertEqual(reply.status_code,404)

    def test_workshop_registration_allows_no_email_without_exposing_link(self):
        anonymous = self.public.post("/api/v1/auth/signup",{
            "workshop_name":"Mobile Registration Garage", "owner_name":"Mobile Owner",
            "mobile":"+919101234567", "whatsapp_consent": True,
        },format="json")
        self.assertEqual(anonymous.status_code,201,anonymous.data)
        self.assertEqual(anonymous.data["user"]["email"],None)
        workshop = Company.objects.get(pk=anonymous.data["workshop"]["id"])
        self.assertEqual(workshop.phone,"+919101234567")
        owner = User.objects.get(pk=anonymous.data["user"]["id"])
        self.assertFalse(owner.has_usable_password())
        self.assertTrue(StaffPasswordInvitation.objects.filter(user=owner).exists())
        self.assertNotIn("setupLink",anonymous.data)


    def test_staff_invitation_never_uses_optional_email(self):
        from unittest.mock import patch
        with patch("apps.accounts.email.queue_email") as email_sender:
            reply = self.create_staff(email="staff@legacy-company.test")
        self.assertEqual(reply.data["whatsappInviteStatus"], "manual")
        self.assertNotIn("emailDeliveryStatus", reply.data)
        email_sender.assert_not_called()

    def test_mobile_forgot_password_has_generic_public_responses(self):
        self.create_staff(consent=False)
        details = {"mobile": "+919333333333", "workshop": "phone-workshop"}
        before = StaffPasswordInvitation.objects.count()
        existing = self.public.post("/api/v1/auth/forgot-password", details, format="json")
        self.assertEqual(existing.status_code, 200, existing.data)
        missing = self.public.post("/api/v1/auth/forgot-password", {
            "mobile": "+919444444444", "workshop": "phone-workshop",
        }, format="json")
        self.assertEqual(missing.status_code, 200, missing.data)
        self.assertEqual(existing.data, missing.data)
        self.assertNotIn("setupLink", existing.data)
        self.assertEqual(StaffPasswordInvitation.objects.count(), before)

    @override_settings(WHATSAPP_SEND_ENABLED=True, CELERY_BROKER_URL="redis://localhost:6379/1")
    def test_registered_mobile_recovery_queues_only_official_workshop_whatsapp(self):
        from apps.whatsapp.models import WhatsAppBusinessConnection, WhatsAppMessageTemplate
        from apps.accounts.staff_setup import resolve_invitation
        from apps.accounts.staff_setup import invitation_url
        reply = self.create_staff(consent=True)
        employee = Employee.objects.select_related("user").get(pk=reply.data["id"])
        old_token = reply.data["setupLink"].rsplit("/", 1)[-1]
        employee.user.set_password("AlreadySetPass33!")
        employee.user.save(update_fields=["password"])
        WhatsAppBusinessConnection.objects.create(
            company=self.company, connected=True, notifications_enabled=True,
            messaging_mode="automatic", phone_number_id="registered-phone",
            waba_id="registered-waba", encrypted_access_token="mock-encrypted",
            event_settings={"staff_onboarding": True},
        )
        WhatsAppMessageTemplate.objects.create(
            company=self.company, event="staff_onboarding",
            template_name="staff_password_setup", approval_status="APPROVED",
        )
        payload = {"mobile": "+919333333333", "workshop": "phone-workshop"}
        first = self.public.post("/api/v1/auth/forgot-password", payload, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        record = StaffPasswordInvitation.objects.filter(user=employee.user).latest("created_at")
        self.assertEqual(record.purpose, "reset")
        self.assertEqual(record.delivery_status, "queued")
        self.assertEqual(resolve_invitation(old_token), None)
        self.assertNotIn("setupLink", first.data)
        self.assertNotIn("token", first.data)
        count = StaffPasswordInvitation.objects.filter(user=employee.user).count()
        retry = self.public.post("/api/v1/auth/forgot-password", payload, format="json")
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(StaffPasswordInvitation.objects.filter(user=employee.user).count(), count)
        # The encrypted token remains absent from the database, API response,
        # and Celery task argument; only the signed URL can be regenerated.
        self.assertNotIn(invitation_url(record), str(record.token_hash))

    @override_settings(WHATSAPP_SEND_ENABLED=True, CELERY_BROKER_URL="redis://localhost:6379/1")
    def test_missing_whatsapp_consent_never_creates_recovery_link(self):
        reply = self.create_staff(consent=False)
        employee = Employee.objects.select_related("user").get(pk=reply.data["id"])
        employee.user.set_password("AlreadySetPass33!")
        employee.user.save(update_fields=["password"])
        before = StaffPasswordInvitation.objects.filter(user=employee.user).count()
        result = self.public.post("/api/v1/auth/forgot-password", {
            "mobile": "+919333333333", "workshop": "phone-workshop",
        }, format="json")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(StaffPasswordInvitation.objects.filter(user=employee.user).count(), before)

    def test_inactive_employment_blocks_login_even_if_user_still_enabled(self):
        reply = self.create_staff()
        employee = Employee.objects.select_related("user").get(pk=reply.data["id"])
        employee.user.set_password("AlreadySetPass33!")
        employee.user.save(update_fields=["password"])
        employee.status = "Inactive"
        employee.save(update_fields=["status"])
        result = self.public.post("/api/v1/auth/login", {
            "identifier": "+919333333333", "password": "AlreadySetPass33!",
        }, format="json")
        self.assertEqual(result.status_code, 400)


    @override_settings(WHATSAPP_SEND_ENABLED=True, CELERY_BROKER_URL="redis://localhost:6379/1")
    def test_official_sender_acceptance_is_recorded_once(self):
        from unittest.mock import patch
        from apps.whatsapp.models import WhatsAppBusinessConnection, WhatsAppMessageTemplate
        from apps.whatsapp.staff_invites import send_staff_invitation, queue_staff_whatsapp_invitation
        from apps.accounts.staff_setup import issue_invitation
        reply = self.create_staff(consent=True)
        employee = Employee.objects.select_related("user").get(pk=reply.data["id"])
        WhatsAppBusinessConnection.objects.create(
            company=self.company, connected=True, notifications_enabled=True,
            messaging_mode="automatic", phone_number_id="official-number",
            waba_id="official-waba", encrypted_access_token="encrypted-credential",
            event_settings={"staff_onboarding": True},
        )
        WhatsAppMessageTemplate.objects.create(
            company=self.company, event="staff_onboarding",
            template_name="approved_staff_onboarding", language="en_US",
            approval_status="APPROVED",
        )
        invite = issue_invitation(employee.user, issuer=self.owner)
        self.assertEqual(queue_staff_whatsapp_invitation(invite, employee), "queued")
        with patch("apps.whatsapp.staff_invites.decrypt_token", return_value="provider-credential"), patch(
            "apps.whatsapp.staff_invites.approved_templates",
            return_value=[{"name": "approved_staff_onboarding", "language": "en_US"}],
        ), patch(
            "apps.whatsapp.staff_invites.send_template_message",
            return_value={"messages": [{"id": "wamid.staff-invitation-once"}]},
        ) as provider:
            self.assertEqual(send_staff_invitation.run(str(invite.pk)), "sent")
            self.assertEqual(send_staff_invitation.run(str(invite.pk)), "skipped")
            provider.assert_called_once()
            self.assertEqual(provider.call_args.args[0], "official-number")
        invite.refresh_from_db()
        self.assertEqual(invite.delivery_status, "sent")
        self.assertEqual(invite.meta_message_id, "wamid.staff-invitation-once")
        self.assertEqual(invite.attempts, 1)
        self.assertEqual(invite.failure_reason, "")
