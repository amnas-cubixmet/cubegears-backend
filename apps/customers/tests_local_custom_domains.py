"""Local hostname routing regression tests for the active wildcard design.

The retired PORTAL_DEV_CUSTOM_DOMAIN_MAP fake-domain mechanism is not a feature
of the current customer-website implementation.
"""
import hashlib
import hmac
import os
import time
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.companies.models import Company
from apps.companies.subdomains import ensure_default_subdomain


@override_settings(DEBUG=True)
class LocalCustomerWildcardTests(TestCase):
    def setUp(self):
        self.secret = "local-wildcard-proxy-secret-long-enough"
        self.alpha = Company.objects.create(name="Alpha Garage", slug="alpha-garage")
        self.beta = Company.objects.create(name="Beta Garage", slug="beta-garage")
        ensure_default_subdomain(self.alpha)
        ensure_default_subdomain(self.beta)
        self.client = APIClient()

    def branding(self, hostname):
        timestamp = str(int(time.time()))
        signature = hmac.new(
            self.secret.encode(),
            (timestamp + ":" + hostname).encode(),
            hashlib.sha256,
        ).hexdigest()
        return self.client.get(
            "/api/v1/customer-website/branding",
            HTTP_X_CG_PORTAL_HOST=hostname,
            HTTP_X_CG_PORTAL_TS=timestamp,
            HTTP_X_CG_PORTAL_SIG=signature,
        )

    def test_distinct_localhost_subdomains_resolve_distinct_companies(self):
        with patch.dict(os.environ, {"PORTAL_PROXY_SHARED_SECRET": self.secret}):
            alpha = self.branding("alpha-garage.localhost:5174")
            beta = self.branding("beta-garage.localhost:5174")
        self.assertEqual(alpha.status_code, 200, alpha.data)
        self.assertEqual(beta.status_code, 200, beta.data)
        self.assertEqual(alpha.data["name"], "Alpha Garage")
        self.assertEqual(beta.data["name"], "Beta Garage")

    def test_unknown_or_inactive_localhost_company_fails_closed(self):
        with patch.dict(os.environ, {"PORTAL_PROXY_SHARED_SECRET": self.secret}):
            self.assertEqual(
                self.branding("unknown.localhost:5174").status_code, 404,
            )
            self.beta.is_active = False
            self.beta.save(update_fields=["is_active"])
            self.assertEqual(
                self.branding("beta-garage.localhost:5174").status_code, 404,
            )

    def test_untrusted_fake_domains_cannot_select_a_tenant(self):
        with patch.dict(os.environ, {
            "PORTAL_PROXY_SHARED_SECRET": self.secret,
            "PORTAL_DEV_CUSTOM_DOMAIN_MAP": '{"alpha-garage.test":"alpha-garage"}',
        }):
            self.assertEqual(
                self.branding("alpha-garage.test:5174").status_code, 404,
            )
