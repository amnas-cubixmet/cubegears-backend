"""Regression coverage for public tenant onboarding and cross-company RBAC."""
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.roles.models import Role


class TenantSecurityTests(APITestCase):
    def setUp(self):
        self.company_a = Company.objects.create(name="Workshop A", slug="workshop-a")
        self.company_b = Company.objects.create(name="Workshop B", slug="workshop-b")
        self.branch_a = Branch.objects.create(company=self.company_a, name="Head Office", code="HO")
        self.branch_b = Branch.objects.create(company=self.company_b, name="Head Office", code="HO")
        self.owner_role = Role.objects.create(
            company=self.company_a, name="Super Admin", code="SUPER_ADMIN",
            permissions=["*"], is_system=True,
        )
        self.foreign_role = Role.objects.create(
            company=self.company_b, name="Super Admin", code="SUPER_ADMIN",
            permissions=["*"], is_system=True,
        )
        self.owner = User.objects.create_user(
            email="owner-a@example.com", password="SafeOwnerPassword923!",
            name="Owner A", company=self.company_a, branch=self.branch_a,
            role=self.owner_role, is_staff=False, is_superuser=False,
        )

    def test_public_signup_creates_tenant_owner_not_platform_superuser(self):
        response = self.client.post(
            "/api/v1/auth/signup",
            {
                "workshop_name": "New Local Workshop",
                "owner_name": "New Owner",
                "mobile": "9876543210",
                "email": "signup-owner@example.com",
                "state": "Kerala",
                "city": "Malappuram",
                "country": "India",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        new_owner = User.objects.select_related("company", "branch", "role").get(
            email="signup-owner@example.com"
        )
        self.assertFalse(new_owner.is_superuser)
        self.assertFalse(new_owner.is_staff)
        self.assertEqual(new_owner.role.code, "SUPER_ADMIN")
        self.assertEqual(new_owner.role.permissions, ["*"])
        self.assertTrue(new_owner.role.is_system)
        self.assertEqual(new_owner.role.company_id, new_owner.company_id)
        self.assertEqual(new_owner.branch.company_id, new_owner.company_id)
        self.assertFalse(new_owner.has_usable_password())

    def test_foreign_company_role_never_grants_permissions(self):
        self.owner.role = self.foreign_role
        self.owner.save(update_fields=["role"])
        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/v1/roles/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_inactive_company_never_grants_permissions(self):
        self.company_a.is_active = False
        self.company_a.save(update_fields=["is_active"])
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get("/api/v1/roles/").status_code, 403)

    def test_platform_superuser_without_company_cannot_read_tenant_api(self):
        platform = User.objects.create_superuser(
            email="platform@example.com", password="SafePlatformPassword923!",
            name="Platform Admin",
        )
        self.client.force_authenticate(platform)
        self.assertEqual(self.client.get("/api/v1/roles/").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/customers/").status_code, 403)

    def test_custom_roles_cannot_grant_wildcard_or_system_owner_code(self):
        self.client.force_authenticate(self.owner)
        forbidden = self.client.post(
            "/api/v1/roles/",
            {"name": "Fake Super", "code": "SUPER_ADMIN", "permissions": ["*"], "is_system": True},
            format="json",
        )
        self.assertEqual(forbidden.status_code, status.HTTP_400_BAD_REQUEST)
        wildcard = self.client.post(
            "/api/v1/roles/",
            {"name": "Owner Copy", "code": "OWNER_COPY", "permissions": ["*"]},
            format="json",
        )
        self.assertEqual(wildcard.status_code, status.HTTP_400_BAD_REQUEST)
        valid = self.client.post(
            "/api/v1/roles/",
            {"name": "Service Advisor", "code": "SERVICE_ADVISOR", "permissions": ["jobs.view"]},
            format="json",
        )
        self.assertEqual(valid.status_code, status.HTTP_201_CREATED, valid.data)
        self.assertFalse(Role.objects.get(id=valid.data["id"]).is_system)

    def test_other_tenant_roles_and_branches_are_not_accessible(self):
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(f"/api/v1/roles/{self.foreign_role.id}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/branches/{self.branch_b.id}").status_code, 404)
        roles = self.client.get("/api/v1/roles/")
        self.assertEqual(roles.status_code, 200)
        self.assertNotIn(str(self.foreign_role.id), str(roles.data))

    def test_company_can_change_profile_but_not_subscription_or_slug(self):
        self.client.force_authenticate(self.owner)
        result = self.client.patch(
            "/api/v1/company/me",
            {"name": "Updated Name", "plan": "enterprise", "slug": "other-tenant", "is_active": False},
            format="json",
        )
        self.assertEqual(result.status_code, 200, result.data)
        self.company_a.refresh_from_db()
        self.assertEqual(self.company_a.name, "Updated Name")
        self.assertEqual(self.company_a.plan, "starter")
        self.assertEqual(self.company_a.slug, "workshop-a")
        self.assertTrue(self.company_a.is_active)
