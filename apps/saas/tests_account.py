from datetime import date
from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.models import Subscription, StorageUsage, CompanySetting


class AccountBackendIntegrationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Test Garage A", slug="test-garage-a", plan="starter",
        )
        self.other = Company.objects.create(
            name="Test Garage B", slug="test-garage-b", plan="starter",
        )
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="ADMIN",
            permissions=[
                "billing.view", "storage.view", "settings.view",
                "settings.manage",
            ],
        )
        self.owner = User.objects.create_user(
            email="owner-account@example.test", password="SecurePassword938!",
            name="Workshop Owner", company=self.company, role=self.role,
        )
        self.colleague = User.objects.create_user(
            email="team-account@example.test", password="SecurePassword938!",
            name="Team User", company=self.company, role=self.role,
        )
        self.inactive = User.objects.create_user(
            email="former-account@example.test", password="SecurePassword938!",
            name="Former User", company=self.company, role=self.role,
            is_active=False,
        )
        self.foreign = User.objects.create_user(
            email="foreign-account@example.test", password="SecurePassword938!",
            name="Other User", company=self.other,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_billing_summary_uses_active_accounts_no_fake_subscription(self):
        response = self.client.get("/api/v1/saas/billing")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["usage"]["seatsUsed"], 2)
        self.assertEqual(response.data["plan"]["name"], "No active subscription")
        self.assertIsNone(response.data["plan"]["basePrice"])
        self.assertIsNone(response.data["plan"]["nextBillingDate"])
        self.assertIsNone(response.data["paymentMethod"])
        self.assertEqual(response.data["invoices"], [])
        self.assertFalse(response.data["billingDataAvailable"])
        # Clients must never rewrite authoritative active-seat counts.
        attempted = self.client.put(
            "/api/v1/saas/billing/usage", {"seatsUsed": 100}, format="json"
        )
        self.assertEqual(attempted.status_code, 405)
        self.assertEqual(self.client.get(
            "/api/v1/saas/billing"
        ).data["usage"]["seatsUsed"], 2)

    def test_active_subscription_value_comes_from_stored_company_subscription(self):
        Subscription.objects.create(
            company=self.company, plan="Business", status="Active",
            seats=4, amount="1499.00",
        )
        result = self.client.get("/api/v1/saas/billing").data
        self.assertEqual(result["plan"]["name"], "Business")
        self.assertEqual(result["plan"]["includedSeats"], 4)
        self.assertEqual(result["plan"]["basePrice"], 1499.0)
        self.assertTrue(result["billingDataAvailable"])
        self.assertEqual(result["usage"]["seatsUsed"], 2)

    def test_storage_history_is_tenant_scoped_and_only_actual_snapshots(self):
        StorageUsage.objects.create(
            company=self.company, date=date(2026, 10, 11),
            category="images", bytes_used=1073741824, file_count=2,
        )
        StorageUsage.objects.create(
            company=self.other, date=date(2026, 10, 11),
            category="images", bytes_used=5368709120, file_count=9,
        )
        rows = self.client.get(
            "/api/v1/saas/storage/history?from=2026-10-01&to=2026-10-31"
        )
        self.assertEqual(rows.status_code, 200)
        self.assertEqual(len(rows.data), 1)
        self.assertEqual(rows.data[0]["usedGb"], 1.0)
        self.assertEqual(rows.data[0]["fileCount"], 2)
        self.assertIsNone(rows.data[0]["charge"])
        self.assertEqual(self.client.get(
            "/api/v1/saas/storage/history?from=2026-10-12"
        ).data, [])
        self.assertEqual(self.client.get(
            "/api/v1/saas/storage/history?from=bad-date"
        ).status_code, 400)
        self.assertEqual(self.client.get(
            "/api/v1/saas/storage/history?from=2026-10-13&to=2026-10-12"
        ).status_code, 400)

    def test_company_updates_are_validated_and_preserve_saved_other_settings(self):
        setting = CompanySetting.objects.create(
            company=self.company, category="company",
            data={"timezone": "Asia/Kolkata", "dateFormat": "DD/MM/YYYY"},
        )
        bad = self.client.put(
            "/api/v1/settings/company",
            {"name": "New Garage", "email": "not-an-email"},
            format="json",
        )
        self.assertEqual(bad.status_code, 400)
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Test Garage A")
        result = self.client.put(
            "/api/v1/settings/company",
            {
                "name": "New Garage", "email": "garage@example.com",
                "gstNo": "32ABCDE1234F1Z5", "phone": "7025831019",
            }, format="json",
        )
        self.assertEqual(result.status_code, 200)
        self.company.refresh_from_db()
        setting.refresh_from_db()
        self.assertEqual(self.company.name, "New Garage")
        self.assertEqual(self.company.gstin, "32ABCDE1234F1Z5")
        self.assertEqual(setting.data["timezone"], "Asia/Kolkata")
        self.assertEqual(setting.data["dateFormat"], "DD/MM/YYYY")
        self.assertEqual(self.client.get(
            "/api/v1/settings/company"
        ).data["name"], "New Garage")

    def test_company_settings_reject_unknown_fields_without_mutating_profile(self):
        before_name = self.company.name
        result = self.client.put(
            "/api/v1/settings/company",
            {"name": "Untrusted Update", "is_active": False},
            format="json",
        )
        self.assertEqual(result.status_code, 400)
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, before_name)
        self.assertTrue(self.company.is_active)
        self.assertIn("Unsupported company fields", result.data["message"])

    def test_company_settings_save_only_validated_fields(self):
        result = self.client.put(
            "/api/v1/settings/company",
            {"name": "  Updated Garage  ", "gstNo": "32ABCDE1234F1Z5"},
            format="json",
        )
        self.assertEqual(result.status_code, 200)
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Updated Garage")
        self.assertEqual(self.company.gstin, "32ABCDE1234F1Z5")
        obj = CompanySetting.objects.get(company=self.company, category="company")
        self.assertEqual(obj.data["name"], "Updated Garage")

    def test_company_locale_settings_remain_writable_and_validated(self):
        result = self.client.put(
            "/api/v1/settings/company",
            {"timezone": "Asia/Dubai", "dateFormat": "YYYY-MM-DD", "currency": "AED"},
            format="json",
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["timezone"], "Asia/Dubai")
        self.assertEqual(result.data["dateFormat"], "YYYY-MM-DD")
        self.company.refresh_from_db()
        self.assertEqual(self.company.currency, "AED")
        bad = self.client.put(
            "/api/v1/settings/company", {"timezone": "Unknown/Zone"}, format="json"
        )
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(
            self.client.get("/api/v1/settings/company").data["timezone"],
            "Asia/Dubai",
        )
