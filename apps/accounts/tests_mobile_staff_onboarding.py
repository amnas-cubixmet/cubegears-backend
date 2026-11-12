"""Regression: OTP-free workshop/mobile login and one-use staff password invitations."""
from datetime import timedelta
from unittest.mock import patch
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
        # First-time setup creates the same verified JWT session as normal
        # mobile/password login, including staff without an email address.
        self.assertTrue(consumed.data.get("access"))
        self.assertTrue(consumed.data.get("refresh"))
        self.assertEqual(consumed.data["user"]["id"], str(employee.user_id))
        self.assertEqual(consumed.data["user"]["phone"], "+919333333333")
        self.assertNotIn("password", consumed.data)
        signed_in_client = APIClient()
        signed_in_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {consumed.data['access']}"
        )
        me = signed_in_client.get("/api/v1/auth/me")
        self.assertEqual(me.status_code, 200, me.data)
        again = self.public.post("/api/v1/auth/staff-invitation/consume", {
            "token":token, "password":"StrongPass23!","confirmPassword":"StrongPass23!",
        },format="json")
        self.assertEqual(again.status_code,400)
        self.assertNotIn("access", again.data)
        self.assertNotIn("refresh", again.data)
        signed_in = self.public.post("/api/v1/auth/login", {
            "identifier":"+919333333333","password":"StrongPass23!",
        },format="json")
        self.assertEqual(signed_in.status_code,200,signed_in.data)
        self.assertEqual(signed_in.data["user"]["id"],str(employee.user_id))
        self.assertFalse(signed_in.data["user"]["is_superuser"])
        self.assertIn("jobs.view",signed_in.data["user"]["effectivePermissions"])

    def test_consume_does_not_issue_session_if_account_access_is_blocked(self):
        from unittest.mock import patch

        reply = self.create_staff()
        token = reply.data["setupLink"].rsplit("/", 1)[-1]
        # The invitation was authorized, but an account access check at the
        # response boundary denies JWT issuance (e.g., role just revoked).
        with patch("apps.accounts.views.user_can_sign_in", return_value=False):
            consumed = self.public.post("/api/v1/auth/staff-invitation/consume", {
                "token": token,
                "password": "StrongPass23!",
                "confirmPassword": "StrongPass23!",
            }, format="json")
        self.assertEqual(consumed.status_code, 200, consumed.data)
        self.assertNotIn("access", consumed.data)
        self.assertNotIn("refresh", consumed.data)
        self.assertNotIn("user", consumed.data)
        self.assertIn("cannot sign in", consumed.data["message"])
        employee = Employee.objects.get(pk=reply.data["id"])
        employee.user.refresh_from_db()
        self.assertTrue(employee.user.check_password("StrongPass23!"))
        self.assertEqual(self.public.post("/api/v1/auth/staff-invitation/consume", {
            "token": token,
            "password": "StrongPass23!",
            "confirmPassword": "StrongPass23!",
        }, format="json").status_code, 400)

    def test_resend_invalidates_old_link_and_expired_is_rejected(self):
        first = self.create_staff()
        employee = Employee.objects.get(pk=first.data["id"])
        old = first.data["setupLink"].rsplit("/",1)[-1]
        # One-minute repeat submissions are idempotent.
        repeat = self.admin.post(f"/api/v1/employees/{employee.pk}/invite",{},format="json")
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(repeat.data["setupLink"], first.data["setupLink"])
        self.assertEqual(StaffPasswordInvitation.objects.filter(user=employee.user).count(), 1)
        # After cooldown, an explicit replacement revokes the old one-use link.
        current = StaffPasswordInvitation.objects.get(user=employee.user)
        StaffPasswordInvitation.objects.filter(pk=current.pk).update(
            created_at=timezone.now() - timedelta(seconds=70),
        )
        new = self.admin.post(f"/api/v1/employees/{employee.pk}/invite",{"replace": True},format="json")
        self.assertEqual(new.status_code,200,new.data)
        self.assertNotEqual(new.data["setupLink"],first.data["setupLink"])
        self.assertEqual(self.public.post("/api/v1/auth/staff-invitation/validate",{"token":old},format="json").status_code,400)
        record = StaffPasswordInvitation.objects.filter(user=employee.user).latest("created_at")
        record.expires_at = timezone.now() - timedelta(seconds=1)
        record.save(update_fields=["expires_at"])
        self.assertEqual(self.public.post("/api/v1/auth/staff-invitation/validate", {
            "token":new.data["setupLink"].rsplit("/",1)[-1],
        },format="json").status_code,400)

    @override_settings(STAFF_WHATSAPP_AUTO_SEND_ENABLED=True)
    def test_manual_whatsapp_reset_stays_manual_even_when_cloud_api_is_enabled(self):
        created = self.create_staff(consent=True)
        employee = Employee.objects.select_related("user").get(pk=created.data["id"])
        employee.user.set_password("ExistingPassword33!")
        employee.user.save(update_fields=["password"])
        with patch("apps.whatsapp.staff_invites.queue_staff_whatsapp_invitation") as queue:
            result = self.admin.post(
                f"/api/v1/employees/{employee.pk}/invite",
                {"manualOnly": True, "replace": True}, format="json",
            )
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["purpose"], "reset")
        self.assertEqual(result.data["whatsappInviteStatus"], "manual")
        self.assertIn("/set-password/", result.data["setupLink"])
        queue.assert_not_called()
        self.assertEqual(
            StaffPasswordInvitation.objects.filter(user=employee.user, invalidated_at__isnull=True).count(), 1
        )
        repeat = self.admin.post(
            f"/api/v1/employees/{employee.pk}/invite",
            {"manualOnly": True, "replace": True}, format="json",
        )
        self.assertEqual(repeat.status_code, 200, repeat.data)
        self.assertEqual(repeat.data["setupLink"], result.data["setupLink"])

    def test_manual_whatsapp_setup_requires_boolean_but_not_api_or_consent(self):
        created = self.create_staff(consent=False)
        employee = Employee.objects.get(pk=created.data["id"])
        ready = self.admin.post(
            f"/api/v1/employees/{employee.pk}/invite",
            {"manualOnly": True}, format="json",
        )
        self.assertEqual(ready.status_code, 200, ready.data)
        self.assertEqual(ready.data["whatsappInviteStatus"], "manual")
        invalid = self.admin.post(
            f"/api/v1/employees/{employee.pk}/invite",
            {"manualOnly": "true"}, format="json",
        )
        self.assertEqual(invalid.status_code, 400)

    def test_failed_setup_link_generation_keeps_created_staff_and_allows_retry(self):
        from unittest.mock import patch
        with patch("apps.employees.views.EmployeeViewSet._issue_staff_invitation",
                   side_effect=ValueError("Temporary signing config issue")):
            created = self.create_staff(consent=False)
        self.assertEqual(created.status_code, 201, created.data)
        self.assertIsNone(created.data["setupLink"])
        self.assertIn("setup link could not", created.data["invitationError"])
        employee = Employee.objects.get(pk=created.data["id"])
        self.assertEqual(Employee.objects.filter(company=self.company, phone=employee.phone).count(), 1)
        retried = self.admin.post(
            f"/api/v1/employees/{employee.pk}/invite",
            {"manualOnly": True}, format="json",
        )
        self.assertEqual(retried.status_code, 200, retried.data)
        self.assertIn("/set-password/", retried.data["setupLink"])
        self.assertEqual(Employee.objects.filter(company=self.company, phone=employee.phone).count(), 1)

    @override_settings(STAFF_WHATSAPP_AUTO_SEND_ENABLED=True)
    def test_manual_share_does_not_reuse_queued_auto_link(self):
        created = self.create_staff(consent=True)
        employee = Employee.objects.select_related("user").get(pk=created.data["id"])
        recent = StaffPasswordInvitation.objects.filter(user=employee.user).latest("created_at")
        recent.delivery_status = "queued"
        recent.save(update_fields=["delivery_status"])
        blocked = self.admin.post(
            f"/api/v1/employees/{employee.pk}/invite",
            {"manualOnly": True, "replace": True}, format="json",
        )
        self.assertEqual(blocked.status_code, 400, blocked.data)
        self.assertIn("manualOnly", blocked.data)
        # After the 60-second cooldown the server can rotate and invalidate
        # the old queued link, instead of sharing one token through both modes.
        StaffPasswordInvitation.objects.filter(pk=recent.pk).update(
            created_at=timezone.now() - timedelta(seconds=70),
        )
        with patch("apps.whatsapp.staff_invites.queue_staff_whatsapp_invitation") as queue:
            renewed = self.admin.post(
                f"/api/v1/employees/{employee.pk}/invite",
                {"manualOnly": True, "replace": True}, format="json",
            )
        self.assertEqual(renewed.status_code, 200, renewed.data)
        self.assertEqual(renewed.data["whatsappInviteStatus"], "manual")
        self.assertNotEqual(renewed.data["setupLink"], created.data["setupLink"])
        queue.assert_not_called()

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

    def test_mixed_case_legacy_email_login_is_case_insensitive(self):
        # UserManager normalizes only the domain and preserves the local part.
        user = User.objects.create_user(
            email="Legacy.Owner@Example.test", password="LegacyOwnerPass123!",
            name="Legacy Case Owner", phone="+919111111191",
            company=self.company, role=self.owner_role,
        )
        result = self.public.post("/api/v1/auth/login", {
            "identifier": "legacy.owner@example.test",
            "password": "LegacyOwnerPass123!",
        }, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["user"]["id"], str(user.pk))

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

    def test_workshop_registration_without_email_returns_manual_owner_setup_link(self):
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
        self.assertEqual(anonymous.data["setupDelivery"], "manual_ready")
        self.assertIn("/set-password/", anonymous.data["setupLink"])


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
    def test_public_staff_recovery_never_queues_messages_even_with_waba(self):
        from unittest.mock import patch
        from apps.whatsapp.models import WhatsAppBusinessConnection, WhatsAppMessageTemplate
        reply = self.create_staff(consent=True)
        employee = Employee.objects.select_related("user").get(pk=reply.data["id"])
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
        before = StaffPasswordInvitation.objects.filter(user=employee.user).count()
        with patch("apps.whatsapp.staff_invites.queue_staff_whatsapp_invitation") as queue:
            existing = self.public.post("/api/v1/auth/forgot-password", {
                "mobile": "+919333333333", "workshop": "phone-workshop",
            }, format="json")
            absent = self.public.post("/api/v1/auth/forgot-password", {
                "mobile": "+919444444444", "workshop": "phone-workshop",
            }, format="json")
        self.assertEqual(existing.status_code, 200)
        self.assertEqual(absent.status_code, 200)
        self.assertEqual(existing.data, absent.data)
        self.assertEqual(StaffPasswordInvitation.objects.filter(user=employee.user).count(), before)
        queue.assert_not_called()
        # Only an authorized administrator may prepare a secure reset capability.
        admin = self.admin.post(f"/api/v1/employees/{employee.pk}/invite", {}, format="json")
        self.assertEqual(admin.status_code, 200, admin.data)
        self.assertEqual(admin.data["whatsappInviteStatus"], "manual")
        self.assertIn("/set-password/", admin.data["setupLink"])

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
    def test_even_legacy_queued_staff_task_cannot_send_whatsapp(self):
        from unittest.mock import patch
        from apps.whatsapp.staff_invites import send_staff_invitation, queue_staff_whatsapp_invitation
        from apps.accounts.staff_setup import issue_invitation
        reply = self.create_staff(consent=True)
        employee = Employee.objects.select_related("user").get(pk=reply.data["id"])
        invitation = issue_invitation(employee.user, issuer=self.owner)
        self.assertEqual(queue_staff_whatsapp_invitation(invitation, employee), "manual")
        self.assertEqual(invitation.delivery_status, "manual")
        # Simulate a Celery job left queued before a manual-only rollout.
        invitation.delivery_status = "queued"
        invitation.save(update_fields=["delivery_status"])
        with patch("apps.whatsapp.staff_invites.send_template_message") as provider:
            self.assertEqual(send_staff_invitation.run(str(invitation.pk)), "manual_only")
            self.assertEqual(send_staff_invitation.run(str(invitation.pk)), "manual_only")
            provider.assert_not_called()
        invitation.refresh_from_db()
        self.assertEqual(invitation.delivery_status, "manual")

    def test_pending_password_setup_cannot_sign_in_using_legacy_magic_link(self):
        from apps.accounts.models import MagicLink
        reply = self.create_staff(email="staff-magic@old-workshop.test")
        employee = Employee.objects.select_related("user").get(pk=reply.data["id"])
        self.assertFalse(employee.user.has_usable_password())
        raw_token = MagicLink.issue(employee.user)
        response = self.public.post("/api/v1/auth/magic-link/verify", {
            "token": raw_token,
        }, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertNotIn("access", response.data)
        # The one-use setup capability must still be usable for this identity.
        setup_token = reply.data["setupLink"].rsplit("/", 1)[-1]
        valid = self.public.post("/api/v1/auth/staff-invitation/validate", {
            "token": setup_token,
        }, format="json")
        self.assertEqual(valid.status_code, 200, valid.data)


    @override_settings(WHATSAPP_SEND_ENABLED=True, CELERY_BROKER_URL="redis://localhost:6379/1")
    def test_incomplete_waba_configuration_requires_manual_share_not_false_queue(self):
        from apps.whatsapp.models import WhatsAppBusinessConnection, WhatsAppMessageTemplate
        WhatsAppBusinessConnection.objects.create(
            company=self.company, connected=True, notifications_enabled=True,
            messaging_mode="automatic", phone_number_id="official-number",
            waba_id="", encrypted_access_token="",
            event_settings={"staff_onboarding": True},
        )
        WhatsAppMessageTemplate.objects.create(
            company=self.company, event="staff_onboarding",
            template_name="approved_staff_onboarding", approval_status="APPROVED",
        )
        reply = self.create_staff(consent=True)
        employee = Employee.objects.get(pk=reply.data["id"])
        self.assertEqual(reply.status_code, 201, reply.data)
        self.assertEqual(reply.data["whatsappInviteStatus"], "manual")
        res = self.admin.post(f"/api/v1/employees/{employee.pk}/invite", {}, format="json")
        # A still-valid manual link can be re-used briefly without duplicate sends.
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["whatsappInviteStatus"], "manual")
        self.assertTrue(res.data["setupLink"])
        self.assertEqual(
            StaffPasswordInvitation.objects.filter(user=employee.user, delivery_status="queued").count(),
            0,
        )

    @override_settings(WHATSAPP_SEND_ENABLED=True, CELERY_BROKER_URL="redis://localhost:6379/1")
    def test_configured_whatsapp_stays_manual_and_never_queues_on_create_or_resend(self):
        from unittest.mock import patch
        from apps.whatsapp.models import WhatsAppBusinessConnection, WhatsAppMessageTemplate
        WhatsAppBusinessConnection.objects.create(
            company=self.company, connected=True, notifications_enabled=True,
            messaging_mode="automatic", phone_number_id="official-number",
            waba_id="official-waba", encrypted_access_token="encrypted-credential",
            event_settings={"staff_onboarding": True},
        )
        WhatsAppMessageTemplate.objects.create(
            company=self.company, event="staff_onboarding",
            template_name="approved_staff_onboarding", approval_status="APPROVED",
        )
        with patch("apps.whatsapp.staff_invites.queue_staff_whatsapp_invitation") as queue, patch(
            "apps.whatsapp.staff_invites.send_staff_invitation.delay"
        ) as sender, patch("apps.accounts.email.queue_email") as email_sender:
            reply = self.create_staff(consent=True)
            self.assertEqual(reply.data["whatsappInviteStatus"], "manual")
            self.assertIn("/set-password/", reply.data["setupLink"])
            employee_id = reply.data["id"]
            resent = self.admin.post(f"/api/v1/employees/{employee_id}/invite", {}, format="json")
        self.assertEqual(resent.status_code, 200, resent.data)
        self.assertEqual(resent.data["whatsappInviteStatus"], "manual")
        self.assertEqual(resent.data["setupLink"], reply.data["setupLink"])
        self.assertEqual(StaffPasswordInvitation.objects.count(), 1)
        queue.assert_not_called()
        sender.assert_not_called()
        email_sender.assert_not_called()

    def test_invitation_url_never_exposed_to_regular_staff_get(self):
        reply = self.create_staff()
        employee_id = reply.data["id"]
        detail = self.admin.get(f"/api/v1/employees/{employee_id}")
        listing = self.admin.get("/api/v1/employees")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertIsNone(detail.data["setupLink"])
        self.assertNotIn(reply.data["setupLink"], str(listing.data))

    def test_revoking_staff_whatsapp_consent_audits_source_and_cancels_queue(self):
        from apps.employees.models import EmployeeActivity
        reply = self.create_staff(consent=True)
        employee = Employee.objects.get(pk=reply.data["id"])
        previous_source = employee.whatsapp_consent_source
        invitation = employee.user.staff_password_invitations.latest("created_at")
        invitation.delivery_status = "queued"
        invitation.save(update_fields=["delivery_status"])
        updated = self.admin.patch(
            f"/api/v1/employees/{employee.pk}",
            {"whatsappConsent": False},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        employee.refresh_from_db()
        invitation.refresh_from_db()
        self.assertFalse(employee.whatsapp_consent)
        self.assertEqual(employee.whatsapp_consent_source, previous_source)
        self.assertEqual(invitation.delivery_status, "manual")
        self.assertTrue(EmployeeActivity.objects.filter(
            employee=employee, action="Staff WhatsApp Consent Revoked", actor=self.owner,
        ).exists())

    def test_retired_webhook_does_not_change_staff_consent(self):
        reply = self.create_staff(consent=True)
        employee = Employee.objects.get(pk=reply.data["id"])
        self.assertTrue(employee.whatsapp_consent)
        result = self.public.post("/api/v1/whatsapp/webhook", {
            "entry": [{"changes": [{"value": {"messages": [{"text": {"body": "STOP"}}]}}]}]
        }, format="json")
        self.assertEqual(result.status_code, 404)
        employee.refresh_from_db()
        self.assertTrue(employee.whatsapp_consent)
