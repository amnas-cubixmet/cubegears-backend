from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.companies.models import Company
from apps.employees.models import Employee
from apps.roles.models import Role


class OwnerManagedRolesTests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Owner Roles Test", slug="owner-roles-test")
        self.owner_role = Role.objects.create(
            company=self.company, name="Super Admin", code="SUPER_ADMIN",
            permissions=["*"], is_system=True,
        )
        self.owner = User.objects.create_user(
            email="roles-owner@example.test", password="StrongPass123!",
            company=self.company, role=self.owner_role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])

        self.manager_role = Role.objects.create(
            company=self.company, name="Manager", code="MANAGER",
            permissions=["company.manage", "staff.create", "staff.view"],
        )
        self.manager = User.objects.create_user(
            email="roles-manager@example.test", password="StrongPass123!",
            company=self.company, role=self.manager_role,
        )
        self.other = Company.objects.create(name="Other Roles Company", slug="other-roles-company")
        self.other_role = Role.objects.create(
            company=self.other, name="Other Workshop Role", code="OTHER",
            permissions=["dashboard.view"],
        )
        self.client.force_authenticate(self.owner)

    def _create(self, name="Technician", code="TECHNICIAN", permissions=None):
        return self.client.post("/api/v1/roles/", {
            "name": name, "code": code,
            "permissions": permissions if permissions is not None else ["dashboard.view", "staff.view"],
            "is_active": True,
        }, format="json")

    def test_owner_can_create_and_edit_role_in_own_company(self):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        role = Role.objects.get(pk=response.data["id"])
        self.assertEqual(role.company_id, self.company.pk)
        self.assertFalse(role.is_system)
        self.assertEqual(role.permissions, ["dashboard.view", "staff.view"])

        updated = self.client.patch(
            f"/api/v1/roles/{role.id}",
            {"name": "Workshop Technician", "permissions": ["dashboard.view"]},
            format="json",
        )
        self.assertEqual(updated.status_code, status.HTTP_200_OK, updated.data)
        role.refresh_from_db()
        self.assertEqual(role.name, "Workshop Technician")
        self.assertEqual(role.code, "TECHNICIAN")
        self.assertEqual(role.permissions, ["dashboard.view"])

    def test_owner_can_edit_and_remove_role_with_not_purchased_paid_permissions(self):
        # A role's permission template is not an entitlement. No Jobs or
        # Invoices purchase is required to remove or configure this role.
        saved = self._create(
            name="Former Workshop Clerk", code="FORMER_CLERK",
            permissions=["jobs.view", "invoices.view"],
        )
        self.assertEqual(saved.status_code, 201, saved.data)
        role_id = saved.data["id"]

        changed = self.client.patch(
            f"/api/v1/roles/{role_id}",
            {"permissions": ["stock.view", "invoices.view"]}, format="json",
        )
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data["permissions"], ["stock.view", "invoices.view"])

        removed = self.client.delete(f"/api/v1/roles/{role_id}")
        self.assertEqual(removed.status_code, 204)
        self.assertFalse(Role.objects.filter(pk=role_id).exists())

    def test_owner_cannot_create_unknown_permission_even_with_paid_role_editing(self):
        unknown = self._create(permissions=["arbitrary.feature.admin"])
        self.assertEqual(unknown.status_code, 400)
        self.assertFalse(Role.objects.filter(company=self.company, code="TECHNICIAN").exists())

    def test_company_manager_with_manage_permission_cannot_mutate_roles(self):
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.get("/api/v1/roles/").status_code, 200)
        self.assertEqual(self._create().status_code, 403)
        self.assertEqual(
            self.client.patch(f"/api/v1/roles/{self.manager_role.pk}", {"name": "Changed"}, format="json").status_code,
            403,
        )
        self.assertEqual(self.client.delete(f"/api/v1/roles/{self.manager_role.pk}").status_code, 403)

    def test_role_list_is_tenant_scoped_and_protected_owner_role_cannot_be_modified(self):
        response = self.client.get("/api/v1/roles/")
        self.assertEqual(response.status_code, 200)
        keys = {role["id"] for role in response.data}
        self.assertNotIn(str(self.other_role.pk), keys)
        self.assertNotIn(str(self.owner_role.pk), keys)
        self.assertEqual(self.client.patch(
            f"/api/v1/roles/{self.owner_role.pk}", {"name": "Unprotected"}, format="json",
        ).status_code, 404)

    def test_assigned_role_cannot_be_deleted_and_unused_role_can(self):
        blocked = self.client.delete(f"/api/v1/roles/{self.manager_role.pk}")
        self.assertEqual(blocked.status_code, 400)
        self.assertTrue(Role.objects.filter(pk=self.manager_role.pk).exists())

        new = self._create()
        self.assertEqual(new.status_code, 201)
        role_id = new.data["id"]
        removed = self.client.delete(f"/api/v1/roles/{role_id}")
        self.assertEqual(removed.status_code, 204)
        self.assertFalse(Role.objects.filter(pk=role_id).exists())

    def test_employee_profile_role_cannot_be_deleted_even_without_login(self):
        saved = self._create(name="Body Technician", code="BODY_TECH")
        self.assertEqual(saved.status_code, 201)
        Employee.objects.create(
            company=self.company, employee_code="EMP-0002",
            name="Profile-only Worker", role_name="Body Technician",
        )
        delete_response = self.client.delete(f'/api/v1/roles/{saved.data["id"]}')
        self.assertEqual(delete_response.status_code, 400)
        self.assertTrue(Role.objects.filter(pk=saved.data["id"]).exists())

    def test_owner_cannot_create_wildcard_platform_billing_or_owner_role(self):
        for permissions in [["*"], ["panel.users.view"], ["billing.edit"]]:
            response = self._create(name="Unsafe", code="UNSAFE", permissions=permissions)
            self.assertEqual(response.status_code, 400)
        response = self._create(name="Spoofed Owner", code="SUPER_ADMIN")
        self.assertEqual(response.status_code, 400)

    def test_role_api_cannot_reach_other_companies(self):
        other_role_url = f"/api/v1/roles/{self.other_role.pk}"
        self.assertEqual(self.client.get(other_role_url).status_code, 404)
        self.assertEqual(self.client.patch(other_role_url, {"name": "Hijacked"}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(other_role_url).status_code, 404)
