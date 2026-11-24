"""Local hostname routing uses the same tenant isolation as production."""
import hashlib
import hmac
import os
import time
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.companies.models import Company


@override_settings(DEBUG=True)
class LocalHostnameTests(TestCase):
    def setUp(self):
        self.secret = "local-custom-domain-proxy-secret-long-enough"
        self.alpha = Company.objects.create(name="Alpha Garage", slug="alpha-garage")
        self.beta = Company.objects.create(name="Beta Garage", slug="beta-garage")
        self.client = APIClient()

    def branding(self, hostname):
        timestamp = str(int(time.time()))
        signature = hmac.new(
            self.secret.encode(), (timestamp + ":" + hostname).encode(), hashlib.sha256
        ).hexdigest()
        return self.client.get(
            "/api/v1/customer-website/branding",
            HTTP_X_CG_PORTAL_HOST=hostname,
            HTTP_X_CG_PORTAL_TS=timestamp,
            HTTP_X_CG_PORTAL_SIG=signature,
        )

    def preview_env(self):
        return patch.dict(os.environ, {
            "PORTAL_PROXY_SHARED_SECRET": self.secret,
        })

    def test_two_local_hostnames_resolve_to_distinct_workshops(self):
        with self.preview_env():
            alpha = self.branding("alpha-garage.localhost:5174")
            beta = self.branding("beta-garage.localhost:5174")
        self.assertEqual(alpha.status_code, 200, alpha.data)
        self.assertEqual(beta.status_code, 200, beta.data)
        self.assertEqual(alpha.data["name"], "Alpha Garage")
        self.assertEqual(beta.data["name"], "Beta Garage")

    def test_unknown_and_inactive_local_hosts_fail_closed(self):
        with self.preview_env():
            self.assertEqual(self.branding("unknown.localhost:5174").status_code, 404)
            self.beta.is_active = False
            self.beta.save(update_fields=["is_active"])
            self.assertEqual(self.branding("beta-garage.localhost:5174").status_code, 404)

    def test_signed_external_hostname_cannot_select_a_tenant_in_development(self):
        with self.preview_env():
            self.assertEqual(self.branding("alpha-garage.test:5174").status_code, 404)

    @override_settings(DEBUG=False)
    def test_local_hostname_is_ignored_in_production(self):
        with self.preview_env():
            self.assertEqual(self.branding("alpha-garage.localhost").status_code, 404)
