"""Logout must revoke refresh tokens even when the access token has expired."""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class LogoutWithExpiredAccessTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Logout Workshop", slug="logout-workshop",
            require_paid_features=False,
        )
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="SUPER_ADMIN",
            is_system=True, is_active=True, permissions=["*"],
        )
        self.user = User.objects.create_user(
            email="logout-workshop@example.test",
            phone="+919855550096", password="StrongStaffPass123!",
            name="Workshop Owner", company=self.company, role=self.role,
        )
        self.client = APIClient()

    def test_logout_revokes_refresh_without_access_header(self):
        refresh = str(RefreshToken.for_user(self.user))
        response = self.client.post(
            "/api/v1/auth/logout", {"refresh": refresh}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        result = self.client.post(
            "/api/v1/auth/refresh", {"refresh": refresh}, format="json",
        )
        self.assertEqual(result.status_code, 401, result.data)

    def test_invalid_access_header_does_not_prevent_refresh_revocation(self):
        refresh = str(RefreshToken.for_user(self.user))
        self.client.credentials(HTTP_AUTHORIZATION="Bearer expired.invalid.jwt")
        response = self.client.post(
            "/api/v1/auth/logout", {"refresh": refresh}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/refresh", {"refresh": refresh}, format="json",
            ).status_code,
            401,
        )

    def test_repeated_or_missing_refresh_is_idempotent(self):
        for body in ({}, {"refresh": "expired.token"}, {"refresh": [1, 2]}):
            response = self.client.post("/api/v1/auth/logout", body, format="json")
            self.assertEqual(response.status_code, 200, response.data)
        # Other private endpoints must remain protected.
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
