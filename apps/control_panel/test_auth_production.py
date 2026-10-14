"""Production-facing SaaS Control Panel authentication regressions."""
from urllib.parse import urlparse

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.models import MagicLink, User
from apps.roles.models import Role


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PASSWORD_RESET_TIMEOUT=3600,
)
class PanelAccountRecoveryTests(TestCase):
    def setUp(self):
        self.role = Role.objects.create(
            name="Platform administrator", code="PLATFORM_ADMIN",
            permissions=["panel.dashboard.view", "panel.users.view"],
        )
        self.user = User.objects.create_user(
            email="platform-admin@test.local", name="Platform Admin",
            password="StrongPlatform2026!", role=self.role, is_staff=True,
            is_active=True,
        )

    def test_magic_link_request_sends_branded_message_and_does_not_reveal_email(self):
        url = reverse("panel:magic-request")
        with self.captureOnCommitCallbacks(execute=True):
            existing = self.client.post(url, {"email": self.user.email})
        self.assertEqual(existing.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        self.assertIn("Sign in", mail.outbox[0].body)
        with self.captureOnCommitCallbacks(execute=True):
            unknown = self.client.post(url, {"email": "missing@test.local"})
        self.assertEqual(unknown.status_code, 200)
        self.assertIn(b"If a Control Panel account exists", unknown.content)
        self.assertEqual(len(mail.outbox), 1)

    def test_magic_link_post_logs_in_directly_and_replay_fails(self):
        token = MagicLink.issue(self.user)
        link = reverse("panel:magic-verify", kwargs={"token": token})
        landing = self.client.get(link)
        self.assertEqual(landing.status_code, 200)
        self.assertEqual(MagicLink.objects.get(user=self.user).used_at, None)
        signed_in = self.client.post(link)
        self.assertEqual(signed_in.status_code, 302)
        self.assertEqual(signed_in.url, reverse("panel:dashboard"))
        self.assertEqual(str(self.client.session["_auth_user_id"]), str(self.user.pk))
        self.client.logout()
        second = self.client.post(link)
        self.assertEqual(second.status_code, 200)
        self.assertIn(b"invalid, expired, or already used", second.content)

    def test_forgot_reset_link_changes_password_without_account_enumeration(self):
        with self.captureOnCommitCallbacks(execute=True):
            result = self.client.post(reverse("panel:forgot"), {"email": self.user.email})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("password-reset/", mail.outbox[0].body)
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        url = reverse("panel:password-reset", kwargs={"uid": uid, "token": token})
        self.assertEqual(self.client.get(url).status_code, 200)
        reset = self.client.post(url, {
            "new_password1": "NewSecret2026!901",
            "new_password2": "NewSecret2026!901",
        })
        self.assertEqual(reset.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewSecret2026!901"))
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertIn(b"invalid or expired", self.client.get(url).content)

    def test_platform_invitation_sets_password_and_starts_session(self):
        invited = User.objects.create_user(
            email="invited@test.local", name="Invited",
            password=None, role=self.role, is_staff=True, is_active=True,
        )
        invited.set_unusable_password()
        invited.save(update_fields=["password"])
        uid = urlsafe_base64_encode(force_bytes(invited.pk))
        token = default_token_generator.make_token(invited)
        url = reverse("panel:password-setup", kwargs={"uid": uid, "token": token})
        response = self.client.post(url, {
            "new_password1": "SetupStrong2026!123",
            "new_password2": "SetupStrong2026!123",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("panel:dashboard"))
        invited.refresh_from_db()
        self.assertTrue(invited.email_verified)
        self.assertTrue(invited.has_usable_password())
        self.assertEqual(self.client.session["_auth_user_id"], str(invited.pk))

    def test_platform_user_creation_invites_without_admin_password(self):
        actor = User.objects.create_superuser(
            email="owner-panel@test.local", name="Platform Owner",
            password="OwnerPlatform2026!",
        )
        self.client.force_login(actor)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("panel:user-create"), {
                "email": "new-panel@test.local", "name": "New Platform Admin",
                "role": str(self.role.pk), "is_active": "on",
                "is_staff": "on",
            })
        self.assertEqual(response.status_code, 302, response.content)
        invited = User.objects.get(email="new-panel@test.local")
        self.assertFalse(invited.has_usable_password())
        self.assertFalse(invited.email_verified)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/login/password-setup/", mail.outbox[0].body)
        self.assertTrue(mail.outbox[0].alternatives)

    def test_non_platform_user_cannot_use_platform_recovery(self):
        tenant = User.objects.create_user(
            email="tenant@test.local", name="Tenant", password="TenantStrong2026!",
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("panel:forgot"), {"email": tenant.email})
        self.assertEqual(len(mail.outbox), 0)
        link = MagicLink.issue(tenant)
        result = self.client.post(reverse("panel:magic-verify", kwargs={"token": link}))
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
