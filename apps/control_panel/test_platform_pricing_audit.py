"""Platform module pricing minimum, audit route and shared layout regression."""
from decimal import Decimal

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.companies.models import Company
from apps.credits.models import CreditLedger, CreditWallet, ModulePriceLabel
from apps.credits.module_pricing import save_module_price
from apps.roles.models import Role
from apps.saas.models import SecurityEvent


class PlatformPricingAuditTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="platform-prices@example.test", password="SafePrices123!",
        )
        self.client.force_login(self.admin)
        self.pricing = reverse("panel:feature-pricing")
        self.audit = reverse("panel:audit-logs")
        self.company = Company.objects.create(name="Live Workshop", slug="audit-live-workshop")
        CreditWallet.objects.create(company=self.company, balance=Decimal("75.00"))

    def pricing_payload(self, amount="5.00"):
        return {
            "module": "jobs", "per_user_credits": amount, "billing_type": "monthly",
            "published": "on",
        }

    def test_minimum_five_credits_applies_to_browser_and_direct_service(self):
        pricing = self.client.get(self.pricing)
        self.assertEqual(pricing.status_code, 200)
        self.assertContains(pricing, "Minimum 5 credits")
        self.assertContains(pricing, 'id="cg-module-search"')
        self.assertContains(pricing, "Storage Pricing")
        self.assertContains(pricing, "Calendar Billing")
        self.assertContains(pricing, "Audit Logs")
        for price in ("0.00", "1", "4.99"):
            self.assertEqual(self.client.post(self.pricing, self.pricing_payload(price)).status_code, 400)
            self.assertFalse(ModulePriceLabel.objects.exists())
        with self.assertRaisesRegex(ValueError, "at least 5"):
            save_module_price({
                "module": "jobs", "per_user_credits": Decimal("4.99"),
                "billing_type": "monthly", "published": True,
            }, self.admin)
        self.assertFalse(CreditLedger.objects.exists())

    def test_price_change_logs_actor_but_never_debits_wallet(self):
        self.assertRedirects(self.client.post(self.pricing, self.pricing_payload("5.00")), self.pricing)
        self.assertEqual(ModulePriceLabel.objects.get(feature__key="jobs").bundle.per_user_credits, Decimal("5.00"))
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("75.00"))
        self.assertFalse(CreditLedger.objects.exists())
        event = SecurityEvent.objects.get(event="FEATURE_PRICE_UPDATED")
        self.assertIsNone(event.company_id)
        self.assertEqual(event.user_id, self.admin.pk)
        self.assertEqual(event.metadata["module"], "jobs")
        self.assertEqual(event.metadata["credits"], "5.00")
        self.assertEqual(self.client.get(self.audit).status_code, 200)
        self.assertContains(self.client.get(self.audit), "FEATURE_PRICE_UPDATED")

    def test_audit_route_requires_platform_security_view(self):
        self.client.logout()
        result = self.client.get(self.audit)
        self.assertEqual(result.status_code, 302)
        self.assertTrue(result.url.startswith("/login/"))

        role = Role.objects.create(
            name="Pricing only", code="PRICING_ONLY",
            permissions=["panel.features.view", "panel.features.edit"],
        )
        restricted = User.objects.create_user(
            email="no-audit@example.test", password="SafePass123!",
            is_staff=True, role=role,
        )
        self.client.force_login(restricted)
        self.assertEqual(self.client.get(self.audit).status_code, 403)

    def test_sign_in_and_post_logout_create_audit_events(self):
        self.client.logout()
        login = self.client.post("/login/", {
            "email": self.admin.email, "password": "SafePrices123!",
        })
        self.assertEqual(login.status_code, 302)
        self.assertTrue(SecurityEvent.objects.filter(event="PANEL_LOGIN", user=self.admin).exists())
        logout = self.client.post("/logout/")
        self.assertRedirects(logout, "/login/", fetch_redirect_response=False)
        self.assertTrue(SecurityEvent.objects.filter(event="PANEL_LOGOUT", user=self.admin).exists())

    def test_stylesheet_shared_and_pricing_search_uses_no_fake_rows(self):
        self.assertIsNotNone(finders.find("control_panel/unified-platform-ui.css"))
        self.assertIsNotNone(finders.find("control_panel/module-pricing.js"))
        pages = (reverse("panel:dashboard"), self.pricing, reverse("panel:storage"))
        for page in pages:
            self.assertContains(self.client.get(page), "unified-platform-ui.css")
        self.assertContains(self.client.get(self.pricing), "module-pricing.js")
