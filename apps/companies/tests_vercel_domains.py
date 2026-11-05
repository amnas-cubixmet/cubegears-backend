"""No-network tenant domain API regression suite."""
import os
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company, CompanyDomain, CompanyDomainAudit
from apps.companies.vercel_domains import (
    VercelDomainError, VercelDomains, certificate_ready, dns_requirements, refresh_domain,
)
from apps.customers.models import Customer
from apps.customers.portal_tokens import ensure_portal_access, issue_link
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class WorkshopDomainTests(TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "VERCEL_CUSTOMER_DOMAIN_TOKEN": "test-server-side-token",
            "VERCEL_CUSTOMER_PROJECT_ID": "prj_customer_test",
            "VERCEL_CUSTOMER_TEAM_ID": "team_customer_test",
            "CUSTOMER_PORTAL_ROOT_DOMAIN": "cubegears.com",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.first = Company.objects.create(name="First", slug="first-tenant")
        self.other = Company.objects.create(name="Second", slug="second-tenant")
        role = Role.objects.create(company=self.first, name="Owner", code="DOM_OWNER_TEST", permissions=["company.manage"], is_active=True)
        second_role = Role.objects.create(company=self.other, name="Owner", code="DOM_OWNER_TEST_TWO", permissions=["company.manage"], is_active=True)
        self.owner = User.objects.create_user(
            email="domain-owner@example.test", password="test-domain-secret",
            name="Domain Owner", company=self.first, role=role,
        )
        self.second_owner = User.objects.create_user(
            email="other-domain@example.test", password="test-domain-secret",
            name="Other Owner", company=self.other, role=second_role,
        )
        self.platform = User.objects.create_superuser(
            email="platform-domain@example.test", password="test-domain-secret", name="Platform Owner",
        )
        for company, owner in ((self.first, self.owner), (self.other, self.second_owner)):
            company.owner = owner
            company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.detail = {
            "name": "portal.example.org", "projectId": "prj_customer_test",
            "apexName": "example.org", "verified": False,
            "verification": [{"type": "TXT", "domain": "_vercel.example.org", "value": "vc-test-challenge"}],
        }
        self.config = {
            "misconfigured": True,
            "recommendedCNAME": [{"rank": 1, "value": "dynamic-target.vercel-dns-042.com"}],
            "recommendedIPv4": [{"rank": 1, "value": ["198.51.100.42"]}],
        }

    def mock_vercel(self, verified=None, misconfigured=None, tls=False):
        detail = dict(self.detail)
        config = dict(self.config)
        if verified is not None:
            detail["verified"] = verified
        if misconfigured is not None:
            config["misconfigured"] = misconfigured
        mocks = [
            patch.object(VercelDomains, "add", return_value=dict(detail)),
            patch.object(VercelDomains, "detail", return_value=detail),
            patch.object(VercelDomains, "configuration", return_value=config),
            patch.object(VercelDomains, "verify", return_value=detail),
            patch.object(VercelDomains, "remove", return_value={}),
            patch("apps.companies.vercel_domains.certificate_ready", return_value=tls),
        ]
        class Scope:
            def __enter__(self_nonlocal):
                self_nonlocal.active = [p.start() for p in mocks]
                return self_nonlocal.active

            def __exit__(self_nonlocal, *_):
                for p in reversed(mocks):
                    p.stop()
        return Scope()

    def create(self):
        return self.client.post(
            "/api/v1/company/customer-website/domains",
            {"hostname": "portal.example.org", "dnsProvider": "cloudflare"}, format="json",
        )

    def test_vercel_provides_real_dns_and_txt_records_without_unverified_activation(self):
        with self.mock_vercel():
            response = self.create()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "pending_verification")
        self.assertFalse(response.data["dnsConfigured"])
        self.assertFalse(response.data["ownershipVerified"])
        self.assertEqual(response.data["dnsRecords"][0], {
            "type": "CNAME", "name": "portal", "value": "dynamic-target.vercel-dns-042.com",
        })
        self.assertEqual(response.data["verificationRecords"][0], {
            "type": "TXT", "name": "_vercel", "value": "vc-test-challenge",
        })
        self.assertEqual(response.data["dnsProvider"], "cloudflare")
        self.assertEqual(CompanyDomainAudit.objects.filter(action="created").count(), 1)

    def test_dns_and_tls_pending_never_activate_domain(self):
        with self.mock_vercel(verified=True, misconfigured=True, tls=False):
            response = self.create()
        self.assertEqual(response.data["status"], "pending_dns")
        domain = CompanyDomain.objects.get()
        with self.mock_vercel(verified=True, misconfigured=False, tls=False):
            checked = self.client.post(f"/api/v1/company/customer-website/domains/{domain.pk}/refresh", {}, format="json")
        self.assertEqual(checked.data["status"], "ssl_pending")
        self.assertEqual(checked.data["sslStatus"], "pending")
        with self.mock_vercel(verified=True, misconfigured=False, tls=True):
            ready = self.client.post(f"/api/v1/company/customer-website/domains/{domain.pk}/verify", {}, format="json")
        self.assertEqual(ready.data["status"], "active")
        self.assertEqual(ready.data["sslStatus"], "ready")
        self.assertTrue(ready.data["verifiedAt"])

    def test_primary_preserves_previous_address_and_magic_link_tracks_verified_domain(self):
        with self.mock_vercel(verified=True, misconfigured=False, tls=True):
            response = self.create()
            domain = CompanyDomain.objects.get()
            primary = self.client.post(f"/api/v1/company/customer-website/domains/{domain.pk}/primary", {}, format="json")
        self.assertEqual(primary.status_code, 200, primary.data)
        self.assertTrue(primary.data["isPrimary"])
        customer = Customer.objects.create(company=self.first, name="Link Customer", phone="+919999900001")
        with patch.dict(os.environ, {"CUSTOMER_WEBSITE_LINKS_ENABLED": "true"}):
            url = issue_link(ensure_portal_access(customer))
        self.assertTrue(url.startswith("https://portal.example.org/access/"))

    def test_other_company_cannot_manage_or_reassign_domain(self):
        with self.mock_vercel():
            self.assertEqual(self.create().status_code, 201)
        domain = CompanyDomain.objects.get()
        self.client.force_authenticate(user=self.second_owner)
        self.assertEqual(self.create().status_code, 409)
        self.assertEqual(self.client.get(f"/api/v1/company/customer-website/domains/{domain.pk}").status_code, 404)
        self.assertEqual(self.client.post(f"/api/v1/company/customer-website/domains/{domain.pk}/verify", {}).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/v1/company/customer-website/domains/{domain.pk}").status_code, 404)

    def test_inactive_domain_cannot_be_primary_or_serve_customer(self):
        with self.mock_vercel(verified=False, misconfigured=True):
            self.create()
        domain = CompanyDomain.objects.get()
        result = self.client.post(f"/api/v1/company/customer-website/domains/{domain.pk}/primary", {}, format="json")
        self.assertEqual(result.status_code, 409)
        self.assertFalse(CompanyDomain.objects.get().is_primary)

    def test_removal_disables_tenant_resolution_and_keeps_domain_audit(self):
        with self.mock_vercel(verified=True, misconfigured=False, tls=True):
            self.create()
        domain = CompanyDomain.objects.get()
        with self.mock_vercel(verified=True, misconfigured=False, tls=True):
            result = self.client.delete(f"/api/v1/company/customer-website/domains/{domain.pk}")
        self.assertEqual(result.status_code, 200, result.data)
        domain.refresh_from_db()
        self.assertEqual(domain.status, "disabled")
        self.assertIsNone(domain.verified_at)
        self.assertTrue(domain.events.filter(action="removed_from_vercel").exists())

    def test_reserved_and_invalid_domains_rejected(self):
        for host in ("first-tenant.cubegears.com", "cubegears.com", "*.example.org",
                     "localhost", "http://example.org", "foo.local", "evil.vercel.app"):
            with self.subTest(host=host):
                self.assertEqual(self.client.post("/api/v1/company/customer-website/domains", {
                    "hostname": host, "dnsProvider": "godaddy",
                }, format="json").status_code, 400)
        self.assertFalse(CompanyDomain.objects.exists())

    def test_server_credentials_required_and_not_returned_to_browser(self):
        with patch.dict(os.environ, {"VERCEL_CUSTOMER_DOMAIN_TOKEN": ""}):
            response = self.create()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "pending_setup")
        self.assertNotIn("test-server-side-token", str(response.data))
        self.assertIn("not configured", response.data["lastError"])

    def test_superadmin_list_and_disable_while_owner_cannot_list_platform_domains(self):
        with self.mock_vercel():
            self.create()
        domain = CompanyDomain.objects.get()
        path = "/api/v1/company/platform/customer-domains"
        self.assertEqual(self.client.get(path).status_code, 403)
        self.client.force_authenticate(user=self.platform)
        result = self.client.get(path)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data[0]["companyId"], str(self.first.pk))
        self.assertEqual(self.client.post(
            f"{path}/{domain.pk}/review", {"action": "disable"}, format="json",
        ).status_code, 200)
        self.assertFalse(CompanyDomain.objects.get().is_approved)
        self.assertTrue(CompanyDomainAudit.objects.filter(action="platform_disabled").exists())

    def test_provider_configuration_and_project_id_are_fenced(self):
        with self.assertRaises(VercelDomainError):
            with patch.object(VercelDomains, "request", return_value={
                "name": "portal.example.org", "projectId": "prj_company_panel",
            }):
                VercelDomains().detail("portal.example.org")
        records, txt = dns_requirements("example.org", {
            "apexName": "example.org", "verification": [],
        }, self.config)
        self.assertEqual(records[0]["type"], "A")
        self.assertEqual(records[0]["value"], "198.51.100.42")
        self.assertEqual(txt, [])

    def test_no_private_ip_tls_probe(self):
        with patch("apps.companies.vercel_domains.socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with patch("apps.companies.vercel_domains.socket.socket") as connect:
                self.assertFalse(certificate_ready("portal.example.org"))
                connect.assert_not_called()
