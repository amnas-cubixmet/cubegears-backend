"""Role matrix rendering, scope validation and delegated staff permissions."""
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role


class RolePermissionsEditorTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            email="permissions-owner@example.com",
            name="Owner", password="SecurePermissions2026!",
        )
        self.company = Company.objects.create(
            name="Permission Garage", slug="permission-garage",
        )
        self.client.force_login(self.owner)

    def test_role_matrix_groups_platform_and_workshop_actions(self):
        role = Role.objects.create(
            company=None, name="SaaS Viewer", code="SAAS_VIEWER",
            permissions=["panel.users.view", "panel.roles.view"],
        )
        response = self.client.get(reverse("panel:role-edit", args=[role.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Module access matrix")
        self.assertContains(response, "SaaS Control Panel")
        self.assertContains(response, "Workshop / Company")
        self.assertContains(response, 'value="panel.users.view"')
        self.assertContains(response, 'value="jobs.view"')
        self.assertContains(response, "Full access (*) — administrator only")
        self.assertIsNotNone(finders.find("control_panel/roles-ui.css"))
        self.assertIsNotNone(finders.find("control_panel/roles-ui.js"))

    def test_roles_directory_offers_distinct_saas_and_workshop_creation(self):
        response = self.client.get(reverse("panel:roles"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add SaaS Admin Staff Role")
        self.assertContains(response, "Add Workshop Role")
        self.assertContains(response, reverse("panel:role-saas-create"))
        self.assertContains(response, "SaaS Admin Staff Roles")
        self.assertContains(response, "Workshop Roles")

    def test_saas_staff_role_creator_is_companyless_and_grants_only_panel_actions(self):
        url = reverse("panel:role-saas-create")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SaaS Admin Staff Role")
        self.assertContains(response, 'data-fixed-scope="platform"')
        self.assertContains(response, 'value="panel.users.view"')
        self.assertNotContains(response, 'value="jobs.view"')
        self.assertContains(response, 'name="company"')
        self.assertContains(response, 'type="hidden"')
        response = self.client.post(url, {
            "name": "SaaS Accounts Operator",
            "code": "SAAS_ACCOUNTS_OPERATOR",
            "permissions": ["panel.dashboard.view", "panel.users.view"],
            "is_active": "on",
        })
        self.assertEqual(response.status_code, 302, response.content)
        role = Role.objects.get(code="SAAS_ACCOUNTS_OPERATOR")
        self.assertIsNone(role.company_id)
        self.assertEqual(role.permissions, ["panel.dashboard.view", "panel.users.view"])

    def test_saas_role_creator_rejects_workshop_company_and_tenant_permissions(self):
        url = reverse("panel:role-saas-create")
        wrong_company = self.client.post(url, {
            "name": "Bad Company Role", "code": "BAD_COMPANY_ROLE",
            "company": str(self.company.pk),
            "permissions": ["panel.users.view"], "is_active": "on",
        })
        self.assertEqual(wrong_company.status_code, 200)
        self.assertContains(wrong_company, "SaaS Admin Staff roles cannot belong to a workshop.")
        self.assertFalse(Role.objects.filter(code="BAD_COMPANY_ROLE").exists())
        wrong_permissions = self.client.post(url, {
            "name": "Bad Access Role", "code": "BAD_ACCESS_ROLE",
            "permissions": ["jobs.view"], "is_active": "on",
        })
        self.assertEqual(wrong_permissions.status_code, 200)
        self.assertFalse(Role.objects.filter(code="BAD_ACCESS_ROLE").exists())
        no_view = self.client.post(url, {
            "name": "No Navigation", "code": "NO_NAVIGATION",
            "permissions": ["panel.users.create"], "is_active": "on",
        })
        self.assertEqual(no_view.status_code, 200)
        self.assertContains(no_view, "Assign at least one SaaS view permission")

    def test_workshop_role_creator_needs_company_and_shows_workshop_matrix(self):
        url = reverse("panel:role-create")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-fixed-scope="workshop"')
        self.assertContains(response, 'value="jobs.view"')
        self.assertNotContains(response, 'value="panel.users.view"')
        invalid = self.client.post(url, {
            "name": "Unscoped Workshop", "code": "UNSCOPED_WORKSHOP",
            "permissions": ["jobs.view"], "is_active": "on",
        })
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "Select a workshop company for this role.")
        self.assertFalse(Role.objects.filter(code="UNSCOPED_WORKSHOP").exists())

    def test_saas_role_creation_requires_platform_role_create_permission(self):
        limited = Role.objects.create(
            company=None, name="Role Reader", code="ROLE_READER",
            permissions=["panel.roles.view"],
        )
        user = User.objects.create_user(
            email="limited-role-reader@example.com", name="Limited",
            password="TestPassword2026!", role=limited, company=None,
            is_staff=True,
        )
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("panel:role-saas-create")).status_code, 403)

    def test_can_save_company_scoped_action_selection(self):
        response = self.client.post(reverse("panel:role-create"), {
            "company": str(self.company.pk),
            "name": "Invoice Operator",
            "code": "INVOICE_OPERATOR",
            "is_active": "on",
            "permissions": ["invoices.view", "invoices.create"],
        })
        self.assertEqual(response.status_code, 302, response.content)
        role = Role.objects.get(code="INVOICE_OPERATOR")
        self.assertEqual(role.company_id, self.company.pk)
        self.assertEqual(role.permissions, ["invoices.view", "invoices.create"])

    def test_cross_scope_permissions_rejected_on_save(self):
        response = self.client.post(reverse("panel:role-create"), {
            "company": str(self.company.pk),
            "name": "Invalid Scope",
            "code": "INVALID_SCOPE",
            "permissions": ["invoices.view", "panel.users.delete"],
            "is_active": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Workshop roles cannot contain SaaS platform permissions.")
        self.assertFalse(Role.objects.filter(code="INVALID_SCOPE").exists())

    def test_limited_platform_staff_cannot_assign_tenant_permissions(self):
        platform_role = Role.objects.create(
            company=None, name="Staff Coordinator", code="STAFF_COORDINATOR",
            permissions=["panel.staff.view", "panel.staff.create"],
        )
        tenant_role = Role.objects.create(
            company=self.company, name="Tenant Owner", code="TENANT_OWNER",
            permissions=["*"],
        )
        actor = User.objects.create_user(
            email="staff-coordinator@example.com", name="Coordinator",
            password="SecureCoordinator2026!", is_staff=True,
            role=platform_role, company=None,
        )
        self.client.force_login(actor)
        response = self.client.post(reverse("panel:staff-create"), {
            "company": str(self.company.pk),
            "employee_code": "LIMIT-100",
            "name": "Unauthorized Staff",
            "email": "unauthorized-staff@example.com",
            "role_name": tenant_role.name,
            "employment_type": "Full Time",
            "base_salary": "0.00",
            "status": "Active",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You cannot assign a workshop role beyond your own permissions.")
        self.assertFalse(User.objects.filter(email="unauthorized-staff@example.com").exists())
