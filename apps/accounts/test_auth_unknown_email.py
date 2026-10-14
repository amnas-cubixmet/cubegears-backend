"""Public authentication must never accept or reveal an unknown email."""
from django.test import TestCase
from rest_framework.test import APIClient


class UnknownEmailAuthenticationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.email = "does-not-exist-918273@example.invalid"

    def test_unknown_email_cannot_login(self):
        response = self.client.post(
            "/api/v1/auth/login",
            {"email": self.email, "password": "AnyPassword123!"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)

    def test_forgot_password_does_not_reveal_unknown_email(self):
        response = self.client.post(
            "/api/v1/auth/forgot-password",
            {"email": self.email},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("email", response.data)

    def test_magic_link_does_not_reveal_unknown_email(self):
        response = self.client.post(
            "/api/v1/auth/magic-link",
            {"email": self.email},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("email", response.data)
