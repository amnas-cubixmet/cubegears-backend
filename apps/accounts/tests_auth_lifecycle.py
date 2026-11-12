"""Isolated HTTP regressions for workshop account authentication and recovery.

No production account, database, SMTP connection, or WhatsApp API is used.
"""
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import MagicLink, User
from apps.companies.models import Company
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
    ACCOUNT_EMAIL_DELIVERY_MODE="smtp",
    EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
    EMAIL_HOST="smtp.example.test",
    EMAIL_HOST_USER="test@example.test",
    EMAIL_HOST_PASSWORD="only-a-test-password",
    DEFAULT_FROM_EMAIL="test@example.test",
    EMAIL_USE_TLS=True,
    EMAIL_USE_SSL=False,
)
class AuthenticationLifecycleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.company = Company.objects.create(
            name="Lifecycle Garage", slug="lifecycle-garage",
            require_paid_features=False,
        )
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="SUPER_ADMIN",
            permissions=["*"], is_active=True,
        )
        self.owner = User.objects.create_user(
            email="Owner@lifecycle.test", phone="+919855552233",
            password="OriginalStrongPass123!", name="Owner",
            company=self.company, role=self.role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])

    def login(self, identifier, password="OriginalStrongPass123!"):
        return self.client.post(
            "/api/v1/auth/login",
            {"identifier": identifier, "password": password}, format="json",
        )

    def extract_query(self, call):
        url = call.kwargs["action_url"]
        return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}

    def test_email_and_mobile_login_reach_same_owner(self):
        for identifier in ("owner@LIFECYCLE.test", "+91 98555 52233"):
            response = self.login(identifier)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["user"]["id"], str(self.owner.pk))
            self.assertTrue(response.data["access"])
            self.assertTrue(response.data["refresh"])

    def test_duplicate_legacy_emails_require_workshop_code_and_do_not_bypass_password(self):
        second = Company.objects.create(
            name="Second Email Workshop",
            slug="second-email-workshop",
            require_paid_features=False,
        )
        role = Role.objects.create(
            company=second, name="Owner", code="SUPER_ADMIN",
            permissions=["*"], is_active=True,
        )
        other = User.objects.create_user(
            email="OWNER@lifecycle.test", phone="+919855552235",
            password="SecondWorkshopPass123!", name="Second Owner",
            company=second, role=role,
        )
        second.owner = other
        second.save(update_fields=["owner"])

        ambiguous = self.login("owner@lifecycle.test")
        self.assertEqual(ambiguous.status_code, 400, ambiguous.data)
        self.assertIn("workshop", ambiguous.data)

        scoped = self.client.post("/api/v1/auth/login", {
            "identifier": "owner@lifecycle.test",
            "password": "OriginalStrongPass123!",
            "workshop": "lifecycle-garage",
        }, format="json")
        self.assertEqual(scoped.status_code, 200, scoped.data)
        self.assertEqual(scoped.data["user"]["id"], str(self.owner.pk))

        scoped_other = self.client.post("/api/v1/auth/login", {
            "identifier": "Owner@LIFECYCLE.test",
            "password": "SecondWorkshopPass123!",
            "workshop": "second-email-workshop",
        }, format="json")
        self.assertEqual(scoped_other.status_code, 200, scoped_other.data)
        self.assertEqual(scoped_other.data["user"]["id"], str(other.pk))

        wrong_password = self.client.post("/api/v1/auth/login", {
            "identifier": "owner@lifecycle.test",
            "password": "WrongPassword123!",
        }, format="json")
        self.assertEqual(wrong_password.status_code, 400, wrong_password.data)
        self.assertNotIn("workshop", wrong_password.data)

        wrong_workshop = self.client.post("/api/v1/auth/login", {
            "identifier": "owner@lifecycle.test",
            "password": "OriginalStrongPass123!",
            "workshop": "second-email-workshop",
        }, format="json")
        self.assertEqual(wrong_workshop.status_code, 400, wrong_workshop.data)

    def test_invalid_unknown_and_pending_password_fail_closed(self):
        wrong = self.login(self.owner.email, "WrongPassword123!")
        unknown = self.login("absent@lifecycle.test", "WrongPassword123!")
        self.assertEqual(wrong.status_code, 400, wrong.data)
        self.assertEqual(wrong.data, unknown.data)
        self.owner.set_unusable_password()
        self.owner.save(update_fields=["password"])
        self.assertEqual(self.login(self.owner.email).status_code, 400)

    def test_disabled_role_blocks_existing_login(self):
        self.role.is_active = False
        self.role.save(update_fields=["is_active"])
        self.assertEqual(self.login(self.owner.email).status_code, 400)

    def test_owner_email_reset_is_one_use_and_revokes_old_jwts(self):
        previous = self.login(self.owner.email)
        self.assertEqual(previous.status_code, 200, previous.data)
        with patch("apps.accounts.views.queue_email") as send:
            requested = self.client.post(
                "/api/v1/auth/forgot-password",
                {"email": "owner@lifecycle.test"}, format="json",
            )
        self.assertEqual(requested.status_code, 200, requested.data)
        self.assertNotIn("token", requested.data)
        send.assert_called_once()
        params = self.extract_query(send.call_args)
        valid = self.client.post("/api/v1/auth/reset-password/validate", {
            "uid": params["uid"], "token": params["token"],
        }, format="json")
        self.assertEqual(valid.status_code, 200, valid.data)
        self.assertTrue(valid.data["valid"])
        updated = self.client.post("/api/v1/auth/reset-password", {
            "uid": params["uid"], "token": params["token"],
            "password": "ReplacementStrongPass123!",
        }, format="json")
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertTrue(updated.data.get("refresh"))
        self.assertEqual(self.login(self.owner.email, "OriginalStrongPass123!").status_code, 400)
        self.assertEqual(self.login(self.owner.email, "ReplacementStrongPass123!").status_code, 200)
        self.assertEqual(self.client.post(
            "/api/v1/auth/refresh", {"refresh": previous.data["refresh"]},
            format="json",
        ).status_code, 401)
        repeated = self.client.post("/api/v1/auth/reset-password", {
            "uid": params["uid"], "token": params["token"],
            "password": "AnotherStrongPass123!",
        }, format="json")
        self.assertEqual(repeated.status_code, 400, repeated.data)
        expired = self.client.post("/api/v1/auth/reset-password/validate", {
            "uid": params["uid"], "token": params["token"],
        }, format="json")
        self.assertEqual(expired.status_code, 400, expired.data)

    @override_settings(PASSWORD_RESET_TIMEOUT=1)
    def test_expired_reset_token_is_rejected(self):
        # Create a genuinely old signed token; patching the clock on the
        # lazy default_token_generator can leave its wrapped instance unchanged.
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        uid = urlsafe_base64_encode(force_bytes(self.owner.pk))
        old_timestamp = (
            default_token_generator._num_seconds(default_token_generator._now())
            - 7200
        )
        stale_token = default_token_generator._make_token_with_timestamp(
            self.owner, old_timestamp, default_token_generator.secret,
        )
        expired = self.client.post("/api/v1/auth/reset-password/validate", {
            "uid": uid, "token": stale_token,
        }, format="json")
        rejected = self.client.post("/api/v1/auth/reset-password", {
            "uid": uid, "token": stale_token,
            "password": "NewStrongPassword123!",
        }, format="json")
        self.assertEqual(expired.status_code, 400, expired.data)
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertTrue(self.owner.check_password("OriginalStrongPass123!"))

    def test_unknown_email_recovery_does_not_dispatch(self):
        with patch("apps.accounts.views.queue_email") as send:
            unknown = self.client.post(
                "/api/v1/auth/forgot-password",
                {"email": "missing@lifecycle.test"}, format="json",
            )
            known = self.client.post(
                "/api/v1/auth/forgot-password",
                {"email": self.owner.email}, format="json",
            )
        self.assertEqual(unknown.status_code, 200, unknown.data)
        self.assertEqual(unknown.data, known.data)
        send.assert_called_once()

    def test_magic_link_is_single_use(self):
        with patch("apps.accounts.views.queue_email") as send:
            result = self.client.post(
                "/api/v1/auth/magic-link", {"email": self.owner.email},
                format="json",
            )
        self.assertEqual(result.status_code, 200, result.data)
        self.assertNotIn("token", result.data)
        token = self.extract_query(send.call_args)["token"]
        first = self.client.post(
            "/api/v1/auth/magic-link/verify", {"token": token}, format="json",
        )
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data["user"]["id"], str(self.owner.pk))
        second = self.client.post(
            "/api/v1/auth/magic-link/verify", {"token": token}, format="json",
        )
        self.assertEqual(second.status_code, 400, second.data)

    def test_expired_magic_link_cannot_login(self):
        with patch("apps.accounts.views.queue_email") as send:
            self.client.post("/api/v1/auth/magic-link",
                             {"email": self.owner.email}, format="json")
        token = self.extract_query(send.call_args)["token"]
        MagicLink.objects.filter(user=self.owner).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        result = self.client.post(
            "/api/v1/auth/magic-link/verify", {"token": token}, format="json",
        )
        self.assertEqual(result.status_code, 400, result.data)

    def test_case_colliding_emails_cannot_trigger_ambiguous_recovery(self):
        User.objects.create_user(
            email="owner@lifecycle.test", phone="+919855552234",
            password="OtherStrongPass123!", name="Legacy Duplicate",
            company=self.company, role=self.role,
        )
        with patch("apps.accounts.views.queue_email") as send:
            reset = self.client.post(
                "/api/v1/auth/forgot-password",
                {"email": "OWNER@lifecycle.test"}, format="json",
            )
            magic = self.client.post(
                "/api/v1/auth/magic-link",
                {"email": "OWNER@lifecycle.test"}, format="json",
            )
        self.assertEqual(reset.status_code, 200, reset.data)
        self.assertEqual(magic.status_code, 200, magic.data)
        self.assertEqual(self.login("OWNER@lifecycle.test").status_code, 400)
        send.assert_not_called()

    @override_settings(ACCOUNT_EMAIL_DELIVERY_MODE="disabled")
    def test_disabled_email_does_not_create_magic_token_or_unusable_signup(self):
        initial = Company.objects.count()
        magic = self.client.post(
            "/api/v1/auth/magic-link",
            {"email": self.owner.email}, format="json",
        )
        self.assertEqual(magic.status_code, 200, magic.data)
        self.assertFalse(MagicLink.objects.filter(user=self.owner).exists())
        signup = self.client.post("/api/v1/auth/signup", {
            "workshop_name": "Stranded Garage", "owner_name": "Owner",
            "mobile": "+919855552239", "email": "stranded@example.test",
        }, format="json")
        self.assertEqual(signup.status_code, 503, signup.data)
        self.assertEqual(Company.objects.count(), initial)

    @override_settings(
        ACCOUNT_EMAIL_DELIVERY_MODE="smtp",
        FRONTEND_URL="http://localhost:5173",
        STAFF_SETUP_ALLOW_LOCAL_HTTP=True,
    )
    def test_owner_signup_email_link_uses_one_time_invitation_flow(self):
        """Owner email setup uses the same real validation API as the React page."""
        with patch("apps.accounts.views.queue_email") as send:
            created = self.client.post("/api/v1/auth/signup", {
                "workshop_name": "Fresh Password Garage",
                "owner_name": "New Workshop Owner",
                "mobile": "+919855552238",
                "email": "new-owner@lifecycle.test",
            }, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertNotIn("setupLink", created.data)
        send.assert_called_once()
        url = send.call_args.kwargs["action_url"]
        self.assertTrue(url.startswith("http://localhost:5173/set-password/"))
        token = url.rsplit("/", 1)[-1]

        verified = self.client.post(
            "/api/v1/auth/staff-invitation/validate",
            {"token": token}, format="json",
        )
        self.assertEqual(verified.status_code, 200, verified.data)
        self.assertEqual(verified.data["accountType"], "owner")
        self.assertEqual(verified.data["purpose"], "setup")
        completed = self.client.post(
            "/api/v1/auth/staff-invitation/consume",
            {
                "token": token,
                "password": "SafeOwnerPassword123!",
                "confirmPassword": "SafeOwnerPassword123!",
            },
            format="json",
        )
        self.assertEqual(completed.status_code, 200, completed.data)
        self.assertTrue(completed.data.get("access"))
        self.assertEqual(
            self.login("new-owner@lifecycle.test", "SafeOwnerPassword123!").status_code,
            200,
        )
        invalidated = self.client.post(
            "/api/v1/auth/staff-invitation/validate",
            {"token": token}, format="json",
        )
        self.assertEqual(invalidated.status_code, 400, invalidated.data)

    def test_setup_password_without_uid_is_clean_400_not_500(self):
        response = self.client.post("/api/v1/auth/setup-password", {
            "email": self.owner.email, "token": "bad-token",
            "password": "AnotherStrongPass123!",
        }, format="json")
        self.assertEqual(response.status_code, 400, response.data)

    def test_recovery_requests_are_throttled(self):
        responses = [
            self.client.post("/api/v1/auth/magic-link",
                             {"email": "absent@lifecycle.test"}, format="json")
            for _ in range(6)
        ]
        self.assertEqual([r.status_code for r in responses[:5]], [200] * 5)
        self.assertEqual(responses[5].status_code, 429)
