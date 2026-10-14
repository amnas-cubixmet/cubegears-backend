"""SaaS control panel login UI and authentication integration checks."""
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.roles.models import Role


class ControlPanelLoginUITests(TestCase):
    def setUp(self):
        self.url = reverse("panel:login")

    def test_login_renders_responsive_assets_accessible_controls_and_csrf(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "control_panel/login.html")
        self.assertContains(response, 'name="viewport"')
        self.assertContains(response, "control_panel/login.css")
        self.assertContains(response, "control_panel/login.js")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, 'autocomplete="email"')
        self.assertContains(response, 'autocomplete="current-password"')
        self.assertContains(response, 'type="button" class="password-toggle"')
        self.assertContains(response, 'aria-controls="id_password"')
        self.assertContains(response, "Sign in to dashboard")

    def test_invalid_credentials_display_error_in_live_auth_form(self):
        response = self.client.post(
            self.url,
            {"email": "invalid@example.com", "password": "incorrect"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid email or password.")
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'name="password"')

    def test_login_csrf_and_redirect_still_work(self):
        account = User.objects.create_superuser(
            email="panel-ui@example.com",
            name="Panel UI",
            password="Test-login-123!",
        )
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(
            client.post(
                self.url,
                {"email": account.email, "password": "Test-login-123!"},
            ).status_code,
            403,
        )
        response = client.get(self.url + "?next=/companies/")
        self.assertEqual(response.status_code, 200)
        csrf_token = client.cookies["csrftoken"].value
        response = client.post(
            self.url + "?next=/companies/",
            {
                "email": account.email,
                "password": "Test-login-123!",
                "csrfmiddlewaretoken": csrf_token,
            },
        )
        self.assertRedirects(response, "/companies/", fetch_redirect_response=False)


class PlatformLoginPermissionTests(TestCase):
    def test_platform_superuser_can_open_control_panel_but_not_tenant_resources(self):
        user = User.objects.create_superuser(
            email="superadmin@example.com",
            name="Platform Admin",
            password="Super-secure-123!",
        )
        self.assertTrue(user_has_permission(user, "panel.dashboard.view"))
        self.assertTrue(user_has_permission(user, "panel.companies.edit"))
        self.assertFalse(user_has_permission(user, "jobs.view"))

        self.assertRedirects(
            self.client.post(
                "/login/",
                {"email": user.email, "password": "Super-secure-123!"},
            ),
            "/",
            fetch_redirect_response=False,
        )
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_delegated_platform_staff_are_limited_to_granted_routes(self):
        role = Role.objects.create(
            name="Platform Reader",
            code="PLATFORM_READER",
            permissions=["panel.companies.view"],
        )
        user = User.objects.create_user(
            email="reader@example.com",
            name="Platform Reader",
            password="Reader-secure-123!",
            is_staff=True,
            role=role,
        )
        self.assertTrue(user_has_permission(user, "panel.companies.view"))
        self.assertFalse(user_has_permission(user, "panel.users.view"))
        self.assertFalse(user_has_permission(user, "jobs.view"))
        self.assertRedirects(
            self.client.post(
                "/login/",
                {"email": user.email, "password": "Reader-secure-123!"},
            ),
            "/companies/",
            fetch_redirect_response=False,
        )
        self.assertEqual(self.client.get("/companies/").status_code, 200)
        self.assertEqual(self.client.get("/users/").status_code, 403)

    def test_company_admin_wildcard_cannot_enter_platform_control_panel(self):
        company = Company.objects.create(name="Company A", slug="company-a")
        role = Role.objects.create(
            company=company,
            name="Company Super Admin",
            code="SUPER_ADMIN",
            permissions=["*"],
        )
        user = User.objects.create_user(
            email="owner@example.com",
            name="Company Owner",
            password="Owner-secure-123!",
            company=company,
            role=role,
            is_staff=True,
        )
        self.assertTrue(user_has_permission(user, "jobs.view"))
        self.assertFalse(user_has_permission(user, "panel.dashboard.view"))
        self.assertFalse(user_has_permission(user, "panel.companies.view"))
        response = self.client.post(
            "/login/",
            {"email": user.email, "password": "Owner-secure-123!"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "This account does not have Control Panel access.",
        )
