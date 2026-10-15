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
