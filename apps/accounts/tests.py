from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import MagicLink, User
from apps.companies.models import Company
from apps.roles.models import Role


class PasswordAuthenticationFlowTests(APITestCase):
    email = "owner@example.com"
    setup_password = "WorkshopPass938!"
    old_password = "OldWorkshop938!"
    new_password = "NewWorkshop482!"

    def make_uid_token(self, user):
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        return uid, token

    def test_setup_password_allows_normal_email_password_login(self):
        user = User.objects.create_user(
            email=self.email,
            password=None,
            name="Workshop Owner",
            is_active=True,
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])

        uid, token = self.make_uid_token(user)

        setup_response = self.client.post(
            "/api/v1/auth/setup-password",
            {
                "uid": uid,
                "token": token,
                "password": self.setup_password,
            },
            format="json",
        )

        self.assertEqual(setup_response.status_code, status.HTTP_200_OK)
        self.assertTrue(setup_response.data.get("access"))
        self.assertTrue(setup_response.data.get("refresh"))
        self.assertEqual(setup_response.data["user"]["email"], self.email)

        login_response = self.client.post(
            "/api/v1/auth/login",
            {
                "email": self.email,
                "password": self.setup_password,
            },
            format="json",
        )

        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        self.assertTrue(login_response.data.get("access"))
        self.assertEqual(login_response.data["user"]["email"], self.email)

    def test_reset_password_replaces_old_password_and_allows_login(self):
        user = User.objects.create_user(
            email=self.email,
            password=self.old_password,
            name="Workshop Owner",
            is_active=True,
        )
        uid, token = self.make_uid_token(user)

        reset_response = self.client.post(
            "/api/v1/auth/reset-password",
            {
                "uid": uid,
                "token": token,
                "password": self.new_password,
            },
            format="json",
        )

        self.assertEqual(reset_response.status_code, status.HTTP_200_OK)
        self.assertTrue(reset_response.data.get("access"))
        self.assertTrue(reset_response.data.get("refresh"))
        self.assertEqual(reset_response.data["user"]["email"], self.email)

        # The reset link cannot create another session after the password change.
        replay = self.client.post(
            "/api/v1/auth/reset-password",
            {"uid": uid, "token": token, "password": "ReplayPassword482!"},
            format="json",
        )
        self.assertEqual(replay.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(replay.data.get("access"))

        old_login = self.client.post(
            "/api/v1/auth/login",
            {
                "email": self.email,
                "password": self.old_password,
            },
            format="json",
        )
        self.assertEqual(old_login.status_code, status.HTTP_400_BAD_REQUEST)

        new_login = self.client.post(
            "/api/v1/auth/login",
            {
                "email": self.email,
                "password": self.new_password,
            },
            format="json",
        )
        self.assertEqual(new_login.status_code, status.HTTP_200_OK)
        self.assertTrue(new_login.data.get("access"))

    def test_reset_does_not_issue_tokens_to_disabled_company_role(self):
        company = Company.objects.create(
            name="Disabled Workshop", slug="disabled-reset-workshop", is_active=False,
        )
        role = Role.objects.create(
            company=company, name="Disabled Owner", code="OWNER",
            permissions=["*"], is_active=True,
        )
        user = User.objects.create_user(
            email="disabled-reset@example.test",
            password=self.old_password, name="Disabled Owner",
            company=company, role=role, is_active=True,
        )
        uid, token = self.make_uid_token(user)
        response = self.client.post(
            "/api/v1/auth/reset-password",
            {"uid": uid, "token": token, "password": self.new_password},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)
        user.refresh_from_db()
        self.assertTrue(user.check_password(self.new_password))

    def test_magic_link_auto_login_is_single_use(self):
        user = User.objects.create_user(
            email=self.email, password=self.old_password,
            name="Owner", is_active=True,
        )
        token = MagicLink.issue(user)
        first = self.client.post(
            "/api/v1/auth/magic-link/verify", {"token": token}, format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertTrue(first.data.get("access"))
        self.assertTrue(first.data.get("refresh"))
        self.assertEqual(first.data["user"]["email"], user.email)

        replay = self.client.post(
            "/api/v1/auth/magic-link/verify", {"token": token}, format="json",
        )
        self.assertEqual(replay.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(replay.data.get("access"))

    def test_magic_link_rejects_disabled_and_cross_company_roles(self):
        company = Company.objects.create(name="Workshop C", slug="magic-workshop-c")
        other = Company.objects.create(name="Workshop D", slug="magic-workshop-d")
        role = Role.objects.create(
            company=company, name="Technician", code="TECH",
            permissions=["jobs.view"], is_active=True,
        )
        user = User.objects.create_user(
            email="magic-tech@example.test", name="Technician",
            password=self.old_password, company=company, role=role,
        )

        role.is_active = False
        role.save(update_fields=["is_active"])
        disabled = self.client.post(
            "/api/v1/auth/magic-link/verify",
            {"token": MagicLink.issue(user)}, format="json",
        )
        self.assertEqual(disabled.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("access", disabled.data)

        role.is_active = True
        role.company = other
        role.save(update_fields=["is_active", "company"])
        crossed = self.client.post(
            "/api/v1/auth/magic-link/verify",
            {"token": MagicLink.issue(user)}, format="json",
        )
        self.assertEqual(crossed.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("refresh", crossed.data)

    def test_password_setup_does_not_auto_login_disabled_company(self):
        company = Company.objects.create(
            name="Setup Workshop", slug="blocked-setup", is_active=False,
        )
        role = Role.objects.create(
            company=company, name="Admin", code="ADMIN",
            permissions=["*"], is_active=True,
        )
        user = User.objects.create_user(
            email="blocked-setup@example.test", name="New User",
            password=None, company=company, role=role,
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])
        uid, token = self.make_uid_token(user)
        response = self.client.post(
            "/api/v1/auth/setup-password",
            {"uid": uid, "token": token, "password": self.setup_password},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)
        user.refresh_from_db()
        self.assertTrue(user.check_password(self.setup_password))

    def test_weak_reset_password_returns_password_field_error(self):
        user = User.objects.create_user(
            email=self.email,
            password=self.old_password,
            name="Workshop Owner",
            is_active=True,
        )
        uid, token = self.make_uid_token(user)

        response = self.client.post(
            "/api/v1/auth/reset-password",
            {
                "uid": uid,
                "token": token,
                "password": "12345678",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data)

    def test_invalid_login_returns_visible_non_field_error(self):
        User.objects.create_user(
            email=self.email,
            password=self.old_password,
            name="Workshop Owner",
            is_active=True,
        )

        response = self.client.post(
            "/api/v1/auth/login",
            {
                "email": self.email,
                "password": "WrongPassword123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("non_field_errors", response.data)


    def test_unknown_email_never_receives_login_tokens(self):
        response = self.client.post(
            "/api/v1/auth/login",
            {"email": "not-in-database@workshop.example", "password": "AnyPassword2026!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data.get("access"))
        self.assertIn("non_field_errors", response.data)

    def test_inactive_database_user_cannot_sign_in(self):
        User.objects.create_user(
            email="inactive@workshop.example",
            password=self.old_password, name="Disabled", is_active=False,
        )
        response = self.client.post(
            "/api/v1/auth/login",
            {"email": "inactive@workshop.example", "password": self.old_password},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data.get("access"))
