"""Security regression tests for the PythonAnywhere HTTPS login origin checks.

Only legitimate same-origin HTTPS form posts are accepted.  Requests with an
opaque/null origin MUST NOT be allowed, even if they have a CSRF cookie.
"""
from django.test import Client, TestCase, override_settings

from apps.accounts.models import User


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    CSRF_TRUSTED_ORIGINS=["https://testserver"],
    SECURE_SSL_REDIRECT=False,
)
class SecureLoginCsrfTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(
            email="csrf-admin@example.com",
            name="CSRF Admin",
            password="Secure-CSRF-Test-932!",
        )

    def fresh_browser(self):
        browser = Client(enforce_csrf_checks=True)
        response = browser.get("/login/?next=/", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Referrer-Policy"], "origin")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertIn("csrftoken", browser.cookies)
        return browser, browser.cookies["csrftoken"].value

    def sign_in(self, browser, csrf_token, **headers):
        return browser.post(
            "/login/?next=/",
            data={
                "email": self.user.email,
                "password": "Secure-CSRF-Test-932!",
                "csrfmiddlewaretoken": csrf_token,
            },
            secure=True,
            **headers,
        )

    def test_same_origin_https_login_accepts_normal_origin(self):
        browser, csrf_token = self.fresh_browser()
        response = self.sign_in(
            browser, csrf_token, HTTP_ORIGIN="https://testserver"
        )
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertIn("_auth_user_id", browser.session)

    def test_https_login_without_origin_uses_same_origin_referer(self):
        browser, csrf_token = self.fresh_browser()
        response = self.sign_in(
            browser, csrf_token, HTTP_REFERER="https://testserver/"
        )
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_null_origin_is_not_trusted(self):
        browser, csrf_token = self.fresh_browser()
        response = self.sign_in(
            browser,
            csrf_token,
            HTTP_ORIGIN="null",
            HTTP_REFERER="https://testserver/login/",
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", browser.session)

    def test_foreign_origin_is_not_trusted(self):
        browser, csrf_token = self.fresh_browser()
        response = self.sign_in(
            browser, csrf_token, HTTP_ORIGIN="https://unauthorized.example"
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", browser.session)

    def test_https_without_origin_or_referer_is_blocked(self):
        browser, csrf_token = self.fresh_browser()
        response = self.sign_in(browser, csrf_token)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", browser.session)
