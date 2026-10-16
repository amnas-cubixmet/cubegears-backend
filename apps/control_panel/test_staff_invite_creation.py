"""Ensure every new staff/admin account receives a private setup invitation."""
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.test_email_helpers import simulate_celery_worker

from apps.accounts.models import User
from apps.companies.models import Company
from apps.employees.models import Employee
from apps.roles.models import Role


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_CELERY_ENABLED=True,
    CELERY_BROKER_URL="redis://127.0.0.1:6379/0",
    FRONTEND_URL="https://app.example.com",
)
class StaffAndAdminInvitationsTests(TestCase):
    def setUp(self):
        simulate_celery_worker(self)
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

    def test_legacy_staff_gets_database_login_and_invite_email(self):
        employee = Employee.objects.create(
            company=self.company, employee_code="LEGACY-15",
            name="Existing Mechanic", email="legacy@example.com",
            role_name="Mechanic", status="Active",
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("panel:staff-invite", args=[employee.pk]))
        self.assertEqual(response.status_code, 302)
        employee.refresh_from_db()
        self.assertIsNotNone(employee.user_id)
        self.assertFalse(employee.user.has_usable_password())
        self.assertEqual(mail.outbox[0].to, ["legacy@example.com"])
        self.assertIn("/setup-password?", mail.outbox[0].body)

    def test_password_email_for_existing_user_sends_reset_not_plain_password(self):
        user = User.objects.create_user(
            email="already-active@example.com", name="Active",
            password="SecurePassword889!", company=self.company,
            role=self.staff_role,
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("panel:user-invite", args=[user.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/reset-password?", mail.outbox[0].body)
        self.assertNotIn("SecurePassword889!", mail.outbox[0].body)
        user.refresh_from_db()
        self.assertTrue(user.check_password("SecurePassword889!"))

    def test_inactive_user_does_not_receive_password_email(self):
        user = User.objects.create_user(
            email="inactive@example.com", name="Inactive",
            company=self.company, role=self.staff_role, is_active=False,
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("panel:user-invite", args=[user.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)
