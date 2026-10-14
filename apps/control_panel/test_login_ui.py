"""SaaS control panel login UI and authentication integration checks."""
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User


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
