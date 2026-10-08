from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User


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
