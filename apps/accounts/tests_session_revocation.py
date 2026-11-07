"""Real JWT sessions must immediately honor revoked workshop access."""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
                   CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class WorkshopSessionRevocationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="JWT Workshop", slug="jwt-workshop", require_paid_features=False,
        )
        self.role = Role.objects.create(
            company=self.company, name="Mechanic", code="JWT_MECHANIC",
            permissions=["dashboard.view"], is_active=True,
        )
        self.user = User.objects.create_user(
            email=None, phone="+919855550001", password="StrongStaffPass123!",
            name="Workshop Staff", company=self.company, role=self.role,
        )
        self.client = APIClient()

    def sign_in(self):
        result = self.client.post("/api/v1/auth/login", {
            "identifier": "+919855550001", "password": "StrongStaffPass123!",
        }, format="json")
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))
        access = result.data["access"]
        refresh = result.data["refresh"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + access)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)
        return refresh

    def test_live_role_revocation_blocks_existing_access_and_refresh(self):
        refresh = self.sign_in()
        self.role.is_active = False
        self.role.save(update_fields=["is_active"])
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        result = self.client.post("/api/v1/auth/refresh", {"refresh": refresh}, format="json")
        self.assertEqual(result.status_code, 401)

    def test_company_disable_blocks_existing_access_and_refresh(self):
        refresh = self.sign_in()
        self.company.is_active = False
        self.company.save(update_fields=["is_active"])
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        result = self.client.post("/api/v1/auth/refresh", {"refresh": refresh}, format="json")
        self.assertEqual(result.status_code, 401)

    def test_disabled_account_blocks_existing_access_and_refresh(self):
        refresh = self.sign_in()
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        result = self.client.post("/api/v1/auth/refresh", {"refresh": refresh}, format="json")
        self.assertIn(result.status_code, (401, 403))

    def test_pending_password_user_cannot_use_synthetic_jwt(self):
        self.user.set_unusable_password()
        self.user.save(update_fields=["password"])
        token = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + str(token.access_token))
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        self.assertEqual(
            self.client.post("/api/v1/auth/refresh", {"refresh": str(token)}, format="json").status_code,
            401,
        )

    def test_valid_staff_can_refresh_to_continue_session(self):
        refresh = self.sign_in()
        result = self.client.post("/api/v1/auth/refresh", {"refresh": refresh}, format="json")
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))
        self.assertTrue(result.data.get("access"))

    def test_expired_bearer_header_cannot_block_public_login_or_refresh(self):
        # A stale JWT must not force clients into a 401 loop before login.
        self.client.credentials(HTTP_AUTHORIZATION="Bearer expired.invalid.jwt")
        login = self.client.post("/api/v1/auth/login", {
            "identifier": "+919855550001", "password": "StrongStaffPass123!",
        }, format="json")
        self.assertEqual(login.status_code, 200, login.data)
        refresh = self.client.post("/api/v1/auth/refresh", {
            "refresh": login.data["refresh"],
        }, format="json")
        self.assertEqual(refresh.status_code, 200, refresh.data)
        self.assertTrue(refresh.data.get("access"))
        # Refreshing a session must never make stale access tokens valid.
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_trailing_slash_login_refresh_and_me_keep_correct_auth_rules(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer expired.invalid.jwt")
        login = self.client.post("/api/v1/auth/login/", {
            "identifier": "+919855550001", "password": "StrongStaffPass123!",
        }, format="json")
        self.assertEqual(login.status_code, 200, login.data)
        refreshed = self.client.post("/api/v1/auth/refresh/", {
            "refresh": login.data["refresh"],
        }, format="json")
        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, 401)
        self.assertEqual(self.client.post("/api/v1/auth/logout/", {
            "refresh": refreshed.data.get("refresh", login.data["refresh"]),
        }, format="json").status_code, 200)
