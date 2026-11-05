"""Website session and tenant-isolation regression tests."""
import hashlib
import hmac
import os
import time
from unittest.mock import patch
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.customers.models import Customer
from apps.customers.portal_tokens import ensure_portal_access, issue_link, rotate_link
from apps.jobs.models import Job
from apps.vehicles.models import Vehicle


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
    CUSTOMER_PORTAL_BASE_URL="https://customer.cubegears.com/c",
)
class CustomerWebsiteTests(TestCase):
    def setUp(self):
        self.secret = "integration-portal-secret-at-least-32-characters-long"
        env = patch.dict(os.environ, {
            "PORTAL_PROXY_SHARED_SECRET": self.secret,
            "CUSTOMER_PORTAL_ROOT_DOMAIN": "cubegears.com",
        })
        env.start()
        self.addCleanup(env.stop)
        self.first = Company.objects.create(name="Alpha Workshop", slug="alpha")
        self.second = Company.objects.create(name="Beta Workshop", slug="beta")
        self.customer = Customer.objects.create(
            company=self.first, name="Portal Customer", phone="+919999999999"
        )
        self.other = Customer.objects.create(
            company=self.second, name="Other Customer", phone="+918888888888"
        )
        vehicle = Vehicle.objects.create(
            company=self.first, customer=self.customer,
            registration="KL10AA5678", make="Toyota", model="Innova"
        )
        Job.objects.create(
            company=self.first, customer=self.customer, vehicle=vehicle,
            job_number="PORTAL-01", status="Inspection", notes="INTERNAL ONLY",
        )
        self.client = APIClient()

    def headers(self, hostname="alpha.cubegears.com"):
        timestamp = str(int(time.time()))
        signature = hmac.new(
            self.secret.encode(), (timestamp + ":" + hostname).encode(),
            hashlib.sha256
        ).hexdigest()
        return {
            "HTTP_X_CG_PORTAL_HOST": hostname,
            "HTTP_X_CG_PORTAL_TS": timestamp,
            "HTTP_X_CG_PORTAL_SIG": signature,
        }

    def _login(self, token=None):
        headers = self.headers()
        self.assertEqual(
            self.client.get("/api/v1/customer-website/branding", **headers).status_code, 200
        )
        csrf = self.client.cookies["cg_portal_csrf"].value
        raw = token or issue_link(ensure_portal_access(self.customer)).rsplit("/", 1)[-1]
        reply = self.client.post(
            "/api/v1/customer-website/session", {"token": raw},
            format="json", HTTP_X_CG_CSRF=csrf, **headers,
        )
        return reply

    def test_only_signed_hostnames_can_resolve_workshops(self):
        path = "/api/v1/customer-website/branding"
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get(path, **self.headers()).status_code, 200)
        bad = self.headers()
        bad["HTTP_X_CG_PORTAL_HOST"] = "beta.cubegears.com"
        self.assertEqual(self.client.get(path, **bad).status_code, 404)
        self.assertEqual(
            self.client.get(path, **self.headers("not-a-workshop.cubegears.com")).status_code, 404
        )

    def test_redeem_link_and_read_existing_customer_data(self):
        self.assertEqual(self._login().status_code, 200)
        result = self.client.get("/api/v1/customer-website/session", **self.headers())
        self.assertEqual(result.status_code, 200, getattr(result, "data", None))
        self.assertEqual(result.data["customer"]["name"], "Portal Customer")
        self.assertEqual(result.data["vehicles"][0]["registration"], "KL10AA5678")
        self.assertEqual(result.data["jobs"][0]["number"], "PORTAL-01")
        self.assertNotIn("INTERNAL ONLY", str(result.data))
        self.assertNotIn("Other Customer", str(result.data))

    def test_cross_tenant_cookie_does_not_grant_access(self):
        self.assertEqual(self._login().status_code, 200)
        result = self.client.get(
            "/api/v1/customer-website/session", **self.headers("beta.cubegears.com")
        )
        self.assertEqual(result.status_code, 401)

    def test_link_from_other_workshop_cannot_be_exchanged(self):
        token = issue_link(ensure_portal_access(self.other)).rsplit("/", 1)[-1]
        self.assertEqual(self._login(token).status_code, 404)

    def test_csrf_header_is_required_for_session_exchange(self):
        self.client.get("/api/v1/customer-website/branding", **self.headers())
        token = issue_link(ensure_portal_access(self.customer)).rsplit("/", 1)[-1]
        response = self.client.post(
            "/api/v1/customer-website/session", {"token": token}, format="json",
            **self.headers()
        )
        self.assertEqual(response.status_code, 403)

    def test_rotating_link_invalidates_existing_session(self):
        self.assertEqual(self._login().status_code, 200)
        rotate_link(ensure_portal_access(self.customer))
        response = self.client.get("/api/v1/customer-website/session", **self.headers())
        self.assertEqual(response.status_code, 401)

    def test_logout_invalidates_browser_session(self):
        self.assertEqual(self._login().status_code, 200)
        response = self.client.post(
            "/api/v1/customer-website/logout", {}, format="json",
            HTTP_X_CG_CSRF=self.client.cookies["cg_portal_csrf"].value, **self.headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.get("/api/v1/customer-website/session", **self.headers()).status_code, 401
        )

    def test_default_tenant_branding_never_contains_private_customer_data(self):
        response = self.client.get(
            "/api/v1/customer-website/branding", **self.headers()
        )
        self.assertEqual(response.data["name"], "Alpha Workshop")
        self.assertNotIn("Portal Customer", str(response.data))
