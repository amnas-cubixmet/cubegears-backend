"""Staff Control Panel UI: directory, safe staff invitation and edit view regressions."""
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.employees.models import Employee
from apps.roles.models import Role


class StaffManagementUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="staff-panel-test-admin@example.com",
            name="Staff Panel Admin", password="StaffPanel-secure-2026!",
        )
        cls.alpha = Company.objects.create(name="Alpha Auto", slug="staff-panel-alpha")
        cls.beta = Company.objects.create(name="Beta Auto", slug="staff-panel-beta")
        cls.branch = Branch.objects.create(company=cls.alpha, name="Main Workshop", code="MAIN")
        cls.role = Role.objects.create(
            company=cls.alpha, name="Mechanic", code="MECHANIC",
            permissions=["jobs.view"], is_active=True, is_system=False,
        )
        cls.account = User.objects.create_user(
            email="mechanic-ui@example.com", name="Linked Mechanic",
            company=cls.alpha, branch=cls.branch, role=cls.role,
            password=None, is_active=True,
        )
        cls.active = Employee.objects.create(
            company=cls.alpha, branch=cls.branch, user=cls.account,
            name="Aisha Mechanic", email="mechanic-ui@example.com",
            employee_code="EMP-101", designation="Mechanic", role_name="Mechanic",
            status="Active",
        )
        cls.inactive = Employee.objects.create(
            company=cls.alpha, name="Binu Technician",
            employee_code="EMP-102", email="binu-ui@example.com",
            status="Inactive",
        )
        cls.other = Employee.objects.create(
            company=cls.beta, name="Other Branch Engineer",
            employee_code="EMP-201", email="engineer-ui@example.com",
            status="Active",
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_staff_directory_has_real_kpis_and_accessible_filters(self):
        response = self.client.get(reverse("panel:staff"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Staff Management")
        self.assertContains(response, "Total Staff")
        self.assertContains(response, "Linked Accounts")
        self.assertContains(response, "Workshop Companies")
        self.assertContains(response, 'class="user-stat-value"', count=4)
        self.assertContains(response, "staff-ui.css")
        self.assertContains(response, "Aisha Mechanic")
        self.assertContains(response, "Binu Technician")
        self.assertContains(response, "Other Branch Engineer")
        self.assertContains(response, 'name="status"')
        self.assertContains(response, 'name="login"')
        self.assertEqual(response.context["total_staff"], 3)
        self.assertEqual(response.context["active_staff"], 2)
        self.assertEqual(response.context["linked_staff"], 1)
        self.assertEqual(response.context["staff_companies"], 2)
        self.assertIsNotNone(finders.find("control_panel/staff-ui.css"))

    def test_combined_search_company_status_and_login_filter(self):
        response = self.client.get(reverse("panel:staff"), {
            "q": "Aisha", "company": str(self.alpha.pk),
            "status": "Active", "login": "linked",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["rows"].paginator.count, 1)
        self.assertContains(response, "Aisha Mechanic")
        self.assertNotContains(response, "Binu Technician")
        self.assertEqual(response.context["total_staff"], 3)

        unlinked = self.client.get(reverse("panel:staff"), {
            "company": str(self.alpha.pk), "login": "unlinked",
        })
        self.assertEqual(unlinked.context["rows"].paginator.count, 1)
        self.assertContains(unlinked, "Binu Technician")
        self.assertNotContains(unlinked, "Other Branch Engineer")

        invalid = self.client.get(reverse("panel:staff"), {
            "status": "not-a-status", "login": "not-a-login",
        })
        self.assertEqual(invalid.context["rows"].paginator.count, 3)
        self.assertEqual(invalid.context["status"], "")
        self.assertEqual(invalid.context["login"], "")

    def test_create_staff_form_has_email_invitation_and_all_fields(self):
        response = self.client.get(reverse("panel:staff-create"))
        self.assertTemplateUsed(response, "control_panel/staff_form.html")
        self.assertContains(response, "Add Staff Member")
        self.assertContains(response, "Workshop &amp; Role")
        self.assertContains(response, "Employment &amp; Compensation")
        self.assertContains(response, "Secure invitation")
        self.assertNotContains(response, 'name="user"')
        for field in ("company", "branch", "name", "employee_code", "email", "phone",
                      "role_name", "designation", "team", "shift", "status", "base_salary",
                      "joining_date", "employment_type", "payment_type"):
            self.assertContains(response, f'name="{field}"')

    def test_create_staff_requires_valid_role_and_uses_password_setup(self):
        result = self.client.post(reverse("panel:staff-create"), {
            "company": str(self.alpha.pk),
            "branch": str(self.branch.pk),
            "name": "Fresh Mechanic", "employee_code": "EMP-103",
            "email": "fresh-mechanic-ui@example.com",
            "role_name": "Mechanic", "employment_type": "Full Time",
            "base_salary": "0.00", "status": "Active",
        })
        self.assertRedirects(result, reverse("panel:staff"))
        worker = Employee.objects.get(employee_code="EMP-103", company=self.alpha)
        self.assertIsNotNone(worker.user_id)
        self.assertEqual(worker.user.company_id, self.alpha.pk)
        self.assertEqual(worker.user.role_id, self.role.pk)
        self.assertFalse(worker.user.has_usable_password())
        self.assertFalse(worker.user.email_verified)

    def test_edit_retains_company_boundaries_and_existing_user(self):
        edit_url = reverse("panel:staff-edit", args=[self.active.pk])
        response = self.client.get(edit_url)
        self.assertTemplateUsed(response, "control_panel/staff_form.html")
        self.assertContains(response, "Edit Staff Member")
        self.assertContains(response, 'name="user"')
        self.assertContains(response, "does not automatically change the permissions")
        self.assertContains(response, "Save Changes")

        invalid = self.client.post(edit_url, {
            "company": str(self.alpha.pk),
            "branch": str(self.branch.pk),
            "user": str(self.account.pk),
            "name": "Renamed Mechanic",
            "employee_code": "EMP-101",
            "role_name": "Mechanic",
            "employment_type": "Full Time",
            "base_salary": "0.00",
            "status": "Active",
        })
        self.assertRedirects(invalid, reverse("panel:staff"))
        self.active.refresh_from_db()
        self.assertEqual(self.active.name, "Renamed Mechanic")
        self.assertEqual(self.active.company_id, self.alpha.pk)
        self.assertEqual(self.active.user_id, self.account.pk)

    def test_details_show_identity_and_login_status(self):
        response = self.client.get(reverse("panel:staff-detail", args=[self.active.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="panel-card staff-profile-header"')
        self.assertContains(response, "Aisha Mechanic")
        self.assertContains(response, "Login linked")
        self.assertContains(response, "EMP-101")

    def test_security_keeps_invite_post_only_and_requires_panel_auth(self):
        url = reverse("panel:staff-invite", args=[self.active.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("panel:staff")).status_code, 302)
        self.assertEqual(self.client.get(reverse("panel:staff-create")).status_code, 302)
        self.assertEqual(self.client.get(reverse("panel:staff-detail", args=[self.active.pk])).status_code, 302)

    def test_other_forms_remain_unmodified(self):
        self.assertTemplateUsed(
            self.client.get(reverse("panel:customer-create")), "control_panel/customer_form.html",
        )
