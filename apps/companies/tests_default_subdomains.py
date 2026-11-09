"""Backend-owned unique wildcard subdomains and optional verified own-domain flow."""
import hashlib
import hmac
import os
import time
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company, CompanyWebsiteSubdomain
from apps.companies.subdomains import (
    assign_subdomain, ensure_default_subdomain, subdomain_label,
)
from apps.customers.models import Customer
from apps.customers.portal_tokens import ensure_portal_access, issue_link
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
    WHATSAPP_SEND_ENABLED=False,
)
class WorkshopSubdomainTests(TestCase):
    def setUp(self):
        self.secret = "subdomain-proxy-test-secret-over-32-characters"
        self.env = patch.dict(os.environ, {
            "CUSTOMER_PORTAL_ROOT_DOMAIN": "cubegears.com",
            "PORTAL_PROXY_SHARED_SECRET": self.secret,
            "CUSTOMER_WEBSITE_LINKS_ENABLED": "true",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.company = Company.objects.create(name="First Garage", slug="first-garage")
        self.other = Company.objects.create(name="Second Garage", slug="second-garage")
        role = Role.objects.create(
            company=self.company, name="Owner", code="OWNER",
            permissions=["*"], is_active=True,
        )
        self.owner = User.objects.create_user(
            email="subdomain-owner@example.test", password="test-password",
            name="Workshop Owner", company=self.company, role=role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def headers(self, host):
        stamp = str(int(time.time()))
        digest = hmac.new(
            self.secret.encode(), (stamp + ":" + host).encode(), hashlib.sha256,
        ).hexdigest()
        return {"HTTP_X_CG_PORTAL_HOST": host,
                "HTTP_X_CG_PORTAL_TS": stamp,
                "HTTP_X_CG_PORTAL_SIG": digest}

    def test_owner_default_created_without_vercel_and_rename_does_not_change_login_slug(self):
        with patch("apps.companies.vercel_domains.VercelDomains") as vercel:
            result = self.client.get("/api/v1/company/customer-website")
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data["subdomain"], "first-garage.cubegears.com")
            self.assertEqual(result.data["subdomainLabel"], "first-garage")
            self.assertTrue(result.data["wildcardReady"])
            changed = self.client.patch(
                "/api/v1/company/customer-website/subdomain",
                {"label": "new-first-garage"}, format="json",
            )
            self.assertEqual(changed.status_code, 200, changed.data)
            self.assertTrue(changed.data["changed"])
            self.assertEqual(changed.data["subdomain"], "new-first-garage.cubegears.com")
            self.assertEqual(subdomain_label(self.company), "new-first-garage")
            self.company.refresh_from_db()
            self.assertEqual(self.company.slug, "first-garage")
            vercel.assert_not_called()

    def test_existing_and_custom_labels_are_reserved_and_unauthorized_updates_denied(self):
        ensure_default_subdomain(self.other)
        for label in ("second-garage", "www", "api", "ab", "bad.name",
                      "invalid_name", "a" * 64):
            with self.subTest(label=label):
                reply = self.client.patch(
                    "/api/v1/company/customer-website/subdomain",
                    {"label": label}, format="json",
                )
                self.assertEqual(reply.status_code, 400, reply.data)
        self.assertEqual(CompanyWebsiteSubdomain.objects.filter(
            company=self.company, label="first-garage"
        ).count(), 1)
        stranger = User.objects.create_user(
            email="stranger-subdomain@example.test", password="test-password",
            name="Non Owner", company=self.company, role=self.owner.role,
        )
        self.client.force_authenticate(stranger)
        self.assertEqual(self.client.patch(
            "/api/v1/company/customer-website/subdomain",
            {"label": "not-my-name"}, format="json",
        ).status_code, 403)

    def test_different_tenants_cannot_resolve_renamed_or_old_signed_hostname(self):
        ensure_default_subdomain(self.company)
        assign_subdomain(self.company, "brand-new")
        self.assertEqual(self.client.get(
            "/api/v1/customer-website/branding",
            **self.headers("brand-new.cubegears.com"),
        ).status_code, 200)
        self.assertEqual(self.client.get(
            "/api/v1/customer-website/branding",
            **self.headers("first-garage.cubegears.com"),
        ).status_code, 404)
        reply = self.client.get(
            "/api/v1/customer-website/branding",
            **self.headers("second-garage.cubegears.com"),
        )
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.data["name"], "Second Garage")

    @override_settings(DEBUG=True)
    def test_local_dev_uses_each_subdomain_localhost_secure_hostname_resolution(self):
        ensure_default_subdomain(self.company)
        assign_subdomain(self.company, "first-local")
        response = self.client.get(
            "/api/v1/customer-website/branding",
            **self.headers("first-local.localhost:3000"),
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["name"], "First Garage")
        self.assertEqual(self.client.get(
            "/api/v1/customer-website/branding",
            **self.headers("unknown.localhost:3000"),
        ).status_code, 404)

    @override_settings(DEBUG=False)
    def test_customer_link_uses_custom_default_label_when_wildcard_enabled(self):
        ensure_default_subdomain(self.company)
        assign_subdomain(self.company, "fresh-wheels")
        customer = Customer.objects.create(
            company=self.company, name="Customer", phone="+919999900008",
        )
        url = issue_link(ensure_portal_access(customer))
        self.assertTrue(url.startswith("https://fresh-wheels.cubegears.com/access/"))

    @override_settings(
        DEBUG=True,
        CUSTOMER_PORTAL_BASE_URL="http://localhost:5174/access",
    )
    def test_myworkshop_localhost_resolves_and_links_to_same_tenant(self):
        """The public localhost site and private tracking link share a tenant."""
        ensure_default_subdomain(self.company)
        assign_subdomain(self.company, "myworkshop")
        response = self.client.get(
            "/api/v1/customer-website/branding",
            **self.headers("myworkshop.localhost:5174"),
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["name"], "First Garage")
        with patch.dict(os.environ, {"CUSTOMER_WEBSITE_LINKS_ENABLED": "false"}):
            customer = Customer.objects.create(
                company=self.company, name="Local Customer", phone="+919999900033",
            )
            link = issue_link(ensure_portal_access(customer))
        self.assertTrue(
            link.startswith("http://myworkshop.localhost:5174/access/"), link
        )
        # Even if the production wildcard flag is enabled by mistake locally,
        # a local workshop must not receive an unusable production tracking URL.
        with patch.dict(os.environ, {"CUSTOMER_WEBSITE_LINKS_ENABLED": "true"}):
            local_link = issue_link(ensure_portal_access(customer))
        self.assertTrue(
            local_link.startswith("http://myworkshop.localhost:5174/access/"),
            local_link,
        )
        # The hostname is tenant-bound. A different workshop has no access.
        response = self.client.get(
            "/api/v1/customer-website/branding",
            **self.headers("unregistered.localhost:5174"),
        )
        self.assertEqual(response.status_code, 404)

    def test_signup_respects_reserved_website_labels_and_creates_one_registry(self):
        ensure_default_subdomain(self.company)
        assign_subdomain(self.company, "popular-garage")
        result = self.client.post("/api/v1/auth/signup", {
            "workshop_name": "Popular Garage",
            "owner_name": "New Owner",
            "mobile": "+919999900022",
            "password": "StrongDirectPass2026!",
            "confirm_password": "StrongDirectPass2026!",
        }, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        new_company = Company.objects.get(pk=result.data["workshop"]["id"])
        self.assertEqual(new_company.slug, "popular-garage-2")
        self.assertEqual(CompanyWebsiteSubdomain.objects.get(company=new_company).label,
                         "popular-garage-2")
