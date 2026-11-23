"""Regression guard for the retired per-company Vercel custom-domain API.

Per-domain Vercel provisioning was replaced by a single platform wildcard and
CompanyWebsiteSubdomain. The old tests imported removed CompanyDomain models,
preventing Django's test discovery from loading this module at all.
"""
from django.test import TestCase


class RetiredVercelDomainRoutesTests(TestCase):
    def test_per_company_domain_routes_are_not_exposed(self):
        for path in (
            "/api/v1/company/customer-website/domains",
            "/api/v1/company/customer-website/domains/1",
            "/api/v1/company/customer-website/domains/1/verify",
            "/api/v1/company/customer-website/domains/1/refresh",
            "/api/v1/company/customer-website/domains/1/primary",
            "/api/v1/company/platform/customer-domains",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
