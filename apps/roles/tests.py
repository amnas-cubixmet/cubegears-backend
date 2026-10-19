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

from apps.roles.legacy_cleanup import LEGACY_SIGNUP_PRESETS


class LegacyAutoRoleCleanupTests(APITestCase):
    """Company signup used to seed five roles, but existing data must be safe."""

    def setUp(self):
        super().setUp()
        OwnerManagedRolesTests.setUp(self)

    def _seed(self, code, *, company=None, permissions=None, name=None):
        original_name, original_permissions = LEGACY_SIGNUP_PRESETS[code]
        return Role.objects.create(
            company=company or self.company,
            code=code,
            name=name or original_name,
            permissions=list(original_permissions) if permissions is None else permissions,
            is_active=True,
            is_system=False,
        )

    def test_owner_preview_only_lists_unchanged_and_unassigned_preset_roles(self):
        mechanic = self._seed("MECHANIC")
        accountant = self._seed("ACCOUNTANT")
        self._seed("PARTS_MANAGER", name="My Custom Parts Role")
        self._seed("QC_INSPECTOR", permissions=["staff.view"])
        advisor = self._seed("SERVICE_ADVISOR")
        Employee.objects.create(
            company=self.company, employee_code="EMP-LEGACY",
            name="Advisor With No Login", role_name=advisor.name,
        )
        self._seed("PARTS_MANAGER", company=self.other)
        response = self.client.get("/api/v1/roles/legacy-presets/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            {row["id"] for row in response.data["roles"]},
            {str(mechanic.id), str(accountant.id)},
        )
        self.assertEqual(Role.objects.filter(company=self.company).count(), 7)
        self.assertIn("Review", response.data["notice"])

    def test_cleanup_is_explicit_and_leaves_owner_custom_and_assigned_roles(self):
        mechanic = self._seed("MECHANIC")
        accountant = self._seed("ACCOUNTANT")
        custom = Role.objects.create(
            company=self.company, name="ss", code="SS",
            permissions=["dashboard.view"], is_active=True,
        )
        assigned = self._seed("SERVICE_ADVISOR")
        Employee.objects.create(
            company=self.company, employee_code="EMP-ASSIGNED",
            name="Assigned Staff", role_name=assigned.name,
        )
        response = self.client.post(
            "/api/v1/roles/legacy-presets/",
            {"roleIds": [str(mechanic.pk), str(accountant.pk)]}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertFalse(Role.objects.filter(pk__in=[mechanic.pk, accountant.pk]).exists())
        for existing in (self.owner_role, self.manager_role, custom, assigned):
            self.assertTrue(Role.objects.filter(pk=existing.pk).exists())
        second = self.client.get("/api/v1/roles/legacy-presets/")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["roles"], [])

    def test_owner_must_review_again_if_record_was_changed_or_assigned(self):
        mechanic = self._seed("MECHANIC")
        accountant = self._seed("ACCOUNTANT", permissions=["staff.view"])
        response = self.client.post(
            "/api/v1/roles/legacy-presets/",
            {"roleIds": [str(mechanic.pk), str(accountant.pk)]}, format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Role.objects.filter(pk=mechanic.pk).exists())
        self.assertTrue(Role.objects.filter(pk=accountant.pk).exists())
        Employee.objects.create(
            company=self.company, employee_code="EMP-NEW",
            name="New Mechanic", role_name=mechanic.name,
        )
        self.assertEqual(self.client.post(
            "/api/v1/roles/legacy-presets/",
            {"roleIds": [str(mechanic.pk)]}, format="json",
        ).status_code, 400)
        self.assertTrue(Role.objects.filter(pk=mechanic.pk).exists())

    def test_non_owner_cannot_preview_or_run_cleanup_and_cross_tenant_ids_fail(self):
        role = self._seed("MECHANIC")
        other_role = self._seed("MECHANIC", company=self.other)
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.get("/api/v1/roles/legacy-presets/").status_code, 403)
        self.assertEqual(self.client.post(
            "/api/v1/roles/legacy-presets/",
            {"roleIds": [str(role.pk)]}, format="json",
        ).status_code, 403)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(
            "/api/v1/roles/legacy-presets/",
            {"roleIds": [str(other_role.pk)]}, format="json",
        ).status_code, 400)
        self.assertTrue(Role.objects.filter(pk=other_role.pk).exists())
        self.assertEqual(self.client.post(
            "/api/v1/roles/legacy-presets/",
            {"roleIds": [str(role.pk), str(role.pk)]}, format="json",
        ).status_code, 400)
