"""Ensure every new staff/admin account receives a private setup invitation."""
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.companies.models import Company
from apps.employees.models import Employee
from apps.roles.models import Role


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://app.example.com",
)
class StaffAndAdminInvitationsTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_superuser(
            email="system-owner@example.com", name="System Owner",
            password="SuperStrongPassword2026!",
        )
        self.client.force_login(self.actor)
        self.company = Company.objects.create(name="Example Garage", slug="example-garage")
        self.staff_role = Role.objects.create(
            company=self.company, name="Mechanic", code="MECHANIC",
            permissions=["jobs.view", "attendance.self"],
        )

    def staff_payload(self, **overrides):
        values = {
            "company": str(self.company.pk),
            "employee_code": "EMP-100",
            "name": "Example Staff",
            "email": "mechanic@example.com",
            "role_name": "Mechanic",
            "employment_type": "Full Time",
            "base_salary": "0.00",
            "status": "Active",
        }
        values.update(overrides)
        return values

    def test_control_panel_staff_create_adds_real_login_and_sends_setup_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("panel:staff-create"), self.staff_payload())
        self.assertEqual(response.status_code, 302, response.content)
        user = User.objects.get(email="mechanic@example.com")
        employee = Employee.objects.get(user=user)
        self.assertEqual(employee.company_id, self.company.pk)
        self.assertEqual(user.role_id, self.staff_role.pk)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["mechanic@example.com"])
        self.assertIn("https://app.example.com/setup-password?", mail.outbox[0].body)

    def test_staff_create_without_registered_email_is_rejected(self):
        response = self.client.post(
            reverse("panel:staff-create"), self.staff_payload(email=""),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Employee.objects.filter(employee_code="EMP-100").exists())
        self.assertFalse(User.objects.filter(email="mechanic@example.com").exists())

    def test_staff_create_rejects_platform_admin_role(self):
        Role.objects.create(
            name="Platform", code="PANEL", permissions=["panel.dashboard.view"],
        )
        response = self.client.post(
            reverse("panel:staff-create"),
            self.staff_payload(role_name="Platform"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Employee.objects.filter(employee_code="EMP-100").exists())

    def test_django_admin_creates_company_user_with_invitation(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("admin:accounts_user_add"), {
                "email": "invited-owner@example.com",
                "name": "Invited Workshop User",
                "company": str(self.company.pk),
                "role": str(self.staff_role.pk),
                "is_active": "on",
                "_save": "Save",
            })
        self.assertEqual(response.status_code, 302, response.content)
        user = User.objects.get(email="invited-owner@example.com")
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertEqual(mail.outbox[0].to, [user.email])
        self.assertIn("https://app.example.com/setup-password?", mail.outbox[0].body)

    def test_django_admin_platform_account_gets_control_panel_link(self):
        panel_role = Role.objects.create(
            name="Platform Reader", code="PLATFORM_READER",
            permissions=["panel.dashboard.view"],
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("admin:accounts_user_add"), {
                "email": "panel-reader@example.com",
                "name": "Panel Reader",
                "role": str(panel_role.pk),
                "is_active": "on",
                "is_staff": "on",
                "_save": "Save",
            })
        self.assertEqual(response.status_code, 302, response.content)
        user = User.objects.get(email="panel-reader@example.com")
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.is_staff)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/login/password-setup/", mail.outbox[0].body)
