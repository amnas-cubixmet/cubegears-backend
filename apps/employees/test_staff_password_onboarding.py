from urllib.parse import parse_qs, urlparse

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.accounts.test_email_helpers import simulate_celery_worker

from apps.accounts.models import User
from apps.companies.models import Company
from apps.employees.models import Employee
from apps.roles.models import Role


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_CELERY_ENABLED=True,
    CELERY_BROKER_URL="redis://127.0.0.1:6379/0",
    FRONTEND_URL="https://workshop.example",
)
class StaffPasswordOnboardingTests(APITestCase):
    def setUp(self):
        simulate_celery_worker(self)
        # DRF throttles use the shared cache; keep isolated signup/login test runs
        # independent of previous authentication test requests.
        cache.clear()
        self.company = Company.objects.create(name="Workshop A", slug="workshop-a")
        self.other = Company.objects.create(name="Workshop B", slug="workshop-b")
        self.admin_role = Role.objects.create(
            company=self.company, name="Owner", code="SUPER_ADMIN",
            is_system=True, permissions=["*"],
        )
        self.staff_role = Role.objects.create(
            company=self.company, name="Mechanic", code="MECHANIC",
            permissions=["jobs.view", "attendance.self"],
        )
        self.owner = User.objects.create_user(
            email="owner@example.com", name="Owner",
            password="SafeOwnerPass483!", company=self.company,
            role=self.admin_role,
        )
        self.client.force_authenticate(self.owner)

    def create_employee(self, email="staff@example.com", role="Mechanic"):
        return self.client.post(
            "/api/v1/employees",
            {"name": "Staff Member", "phone": "9876543210",
             "email": email, "designation": "Mechanic", "role": role},
            format="json",
        )

    def test_new_staff_gets_password_setup_then_tokens_for_same_company(self):
        with self.captureOnCommitCallbacks(execute=True):
            result = self.create_employee()
        self.assertEqual(result.status_code, 201, result.data)
        employee = Employee.objects.get(pk=result.data["id"])
        self.assertIsNotNone(employee.user_id)
        self.assertEqual(employee.user.company_id, self.company.id)
        self.assertEqual(employee.user.role_id, self.staff_role.id)
        self.assertFalse(employee.user.has_usable_password())
        self.assertEqual(result.data["loginStatus"], "Password Setup Pending")
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("https://workshop.example/setup-password?", body)
        url = next(token for token in body.split() if token.startswith("https://workshop.example/setup-password?"))
        params = parse_qs(urlparse(url).query)
        setup = self.client.post(
            "/api/v1/auth/setup-password",
            {"uid": params["uid"][0], "token": params["token"][0],
             "password": "NewStaffSecurePass489!"},
            format="json",
        )
        self.assertEqual(setup.status_code, 200, setup.data)
        self.assertTrue(setup.data["access"])
        self.assertEqual(setup.data["user"]["company"]["id"], str(self.company.id))
        self.assertEqual(setup.data["user"]["role"]["code"], "MECHANIC")
        self.assertIn("jobs.view", setup.data["user"]["effectivePermissions"])
        self.assertNotIn("payroll.view", setup.data["user"]["effectivePermissions"])
        login = self.client.post(
            "/api/v1/auth/login",
            {"email": "staff@example.com", "password": "NewStaffSecurePass489!"},
            format="json",
        )
        self.assertEqual(login.status_code, 200, login.data)
        self.assertEqual(login.data["user"]["company"]["id"], str(self.company.id))
        self.assertEqual(self.create_employee().status_code, 400)

    def test_invalid_role_and_cross_company_email_cannot_create_login(self):
        User.objects.create_user(email="taken@example.com", name="Other", company=self.other)
        self.assertEqual(self.create_employee(email="taken@example.com").status_code, 400)
        self.assertEqual(self.create_employee(role="Super Admin").status_code, 400)
        self.assertEqual(self.create_employee(role="Nonexistent").status_code, 400)
        self.assertEqual(User.objects.filter(email="staff@example.com").count(), 0)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_console_backend_reports_no_inbox_delivery_on_create(self):
        from contextlib import redirect_stdout
        from io import StringIO
        # Console output contains signed setup credentials and is never logged.
        with redirect_stdout(StringIO()):
            with self.captureOnCommitCallbacks(execute=True):
                created = self.create_employee()
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["emailDeliveryStatus"], "pending")
        self.assertTrue(Employee.objects.filter(pk=created.data["id"]).exists())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_resend_invitation_reports_console_only_not_sent(self):
        from contextlib import redirect_stdout
        from io import StringIO
        with redirect_stdout(StringIO()):
            with self.captureOnCommitCallbacks(execute=True):
                created = self.create_employee()
            with self.captureOnCommitCallbacks(execute=True):
                invited = self.client.post(f"/api/v1/employees/{created.data['id']}/invite")
        self.assertEqual(invited.status_code, 200, invited.data)
        self.assertEqual(invited.data["emailDeliveryStatus"], "pending")
        self.assertIn("pending transaction commit", invited.data["message"].lower())

    def test_without_email_creates_profile_but_no_account_or_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            result = self.create_employee(email="")
        self.assertEqual(result.status_code, 201, result.data)
        self.assertIsNone(Employee.objects.get(pk=result.data["id"]).user)
        self.assertEqual(len(mail.outbox), 0)

    def test_company_non_admin_cannot_create_staff_login(self):
        non_admin = User.objects.create_user(
            email="editor@example.com", name="Editor", company=self.company,
            role=Role.objects.create(company=self.company, name="Staff Editor", code="EDITOR",
                                     permissions=["staff.create", "staff.view", "jobs.view", "attendance.self"]),
            password="SafeEditorPass-923!",
        )
        self.client.force_authenticate(non_admin)
        self.assertEqual(self.create_employee().status_code, 403)
        self.assertFalse(User.objects.filter(email="staff@example.com").exists())

    def test_resend_setup_link_does_not_grant_extra_permissions(self):
        with self.captureOnCommitCallbacks(execute=True):
            created = self.create_employee()
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            invited = self.client.post(f"/api/v1/employees/{created.data['id']}/invite")
        self.assertEqual(invited.status_code, 200, invited.data)
        self.assertEqual(invited.data["loginStatus"], "Password Setup Pending")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/setup-password?", mail.outbox[0].body)

    def test_new_workshop_signup_has_no_automatic_staff_roles(self):
        self.client.force_authenticate(user=None)
        with self.captureOnCommitCallbacks(execute=True):
            result = self.client.post("/api/v1/auth/signup", {
                "workshop_name": "New Auto Workshop", "owner_name": "Workshop Owner",
                "mobile": "9876543210", "email": "new-owner@example.com",
                "country": "India", "state": "Kerala", "city": "Malappuram",
            }, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        company = Company.objects.get(pk=result.data["workshop"]["id"])
        self.assertTrue(Role.objects.filter(company=company, code="SUPER_ADMIN", is_system=True).exists())
        self.assertFalse(Role.objects.filter(company=company, is_system=False).exists())

    def test_authentication_never_seeds_or_overwrites_custom_roles(self):
        legacy = Company.objects.create(name="Legacy Workshop", slug="legacy-workshop")
        custom = Role.objects.create(company=legacy, name="Custom Mechanic", code="MECHANIC", permissions=["attendance.self"])
        staff = User.objects.create_user(email='custom-staff@example.test', company=legacy, role=custom, password='CustomStaffSecure123!')
        self.client.force_authenticate(staff)
        for _ in range(2):
            self.assertEqual(self.client.get('/api/v1/auth/me').status_code, 200)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post('/api/v1/auth/login', {'email':staff.email,'password':'CustomStaffSecure123!'}, format='json').status_code, 200)
        custom.refresh_from_db()
        staff.refresh_from_db()
        self.assertEqual(custom.permissions, ['attendance.self'])
        self.assertEqual(staff.role_id, custom.pk)
        self.assertEqual(Role.objects.filter(company=legacy).count(), 1)

    def test_staff_login_rejects_inactive_or_cross_company_role(self):
        with self.captureOnCommitCallbacks(execute=True):
            created = self.create_employee()
        self.assertEqual(created.status_code, 201, created.data)
        employee = Employee.objects.get(pk=created.data["id"])
        employee.user.set_password("SecureMechanicPass483!")
        employee.user.save(update_fields=["password"])
        self.client.force_authenticate(user=None)

        def login():
            return self.client.post("/api/v1/auth/login", {
                "email": employee.user.email,
                "password": "SecureMechanicPass483!",
            }, format="json")

        self.assertEqual(login().status_code, 200)
        self.staff_role.is_active = False
        self.staff_role.save(update_fields=["is_active"])
        self.assertEqual(login().status_code, 400)
        self.staff_role.is_active = True
        self.staff_role.company = self.other
        self.staff_role.save(update_fields=["is_active", "company"])
        self.assertEqual(login().status_code, 400)

    def test_new_password_setup_link_is_single_use(self):
        with self.captureOnCommitCallbacks(execute=True):
            created = self.create_employee(email="new-mechanic@example.com")
        self.assertEqual(created.status_code, 201, created.data)
        url = next(part for part in mail.outbox[-1].body.split()
                   if part.startswith("https://workshop.example/setup-password?"))
        params = parse_qs(urlparse(url).query)
        payload = {
            "uid": params["uid"][0], "token": params["token"][0],
            "password": "VeryStrongStaffPassword927!",
        }
        first = self.client.post("/api/v1/auth/setup-password", payload, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        again = self.client.post("/api/v1/auth/setup-password", payload, format="json")
        self.assertEqual(again.status_code, 400, again.data)

    def test_invalid_password_login_returns_user_friendly_validation_error(self):
        with self.captureOnCommitCallbacks(execute=True):
            created = self.create_employee()
        self.assertEqual(created.status_code, 201, created.data)
        self.client.force_authenticate(user=None)
        response = self.client.post("/api/v1/auth/login", {
            "email": "staff@example.com", "password": "wrong-password",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid email or password", str(response.data))
