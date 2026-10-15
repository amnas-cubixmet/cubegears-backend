"""Platform storage pricing UI must be real, permissioned and ledger-safe."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.companies.models import Company
from apps.credits.models import CreditLedger, CreditWallet, StorageCreditPolicy
from apps.credits.storage_billing import charge_storage_period
from apps.roles.models import Role
from apps.saas.models import MediaFile


class StorageRatePanelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="storage-platform@example.test", name="Platform Owner",
            password="StrongStorageTest123!",
        )
        cls.company = Company.objects.create(name="Actual Storage Garage", slug="storage-garage")
        cls.other = Company.objects.create(name="Other Storage Garage", slug="other-storage-garage")
        MediaFile.objects.create(
            company=cls.company, file="saas/real-upload.bin", name="Real Upload",
            size_bytes=2 * (1024 ** 3),
        )
        MediaFile.objects.create(
            company=cls.other, file="saas/another-upload.bin", name="Another",
            size_bytes=512 * (1024 ** 2),
        )
        CreditWallet.objects.create(company=cls.company, balance=Decimal("40.00"))
        cls.url = reverse("panel:storage")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_page_shows_real_storage_not_doubled_snapshot_totals(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Per-GB Storage Pricing")
        self.assertContains(response, "Save Storage Pricing")
        self.assertContains(response, 'name="credits_per_gb"')
        self.assertContains(response, 'name="included_gb"')
        self.assertContains(response, 'name="billing_period"')
        self.assertContains(response, 'name="is_active"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        by_company = {row["company"].pk: row for row in response.context["live_storage_rows"]}
        self.assertEqual(by_company[self.company.pk]["used_gb"], Decimal("2.000"))
        self.assertEqual(by_company[self.other.pk]["used_gb"], Decimal("0.500"))
        self.assertEqual(response.context["snapshot_count"], 0)
        self.assertEqual(CreditLedger.objects.count(), 0)

    def test_save_rate_updates_policy_but_never_deducts_credits(self):
        response = self.client.post(self.url, {
            "credits_per_gb": "12.50",
            "included_gb": "1.000",
            "billing_period": "monthly",
            "is_active": "on",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        policy = StorageCreditPolicy.objects.get()
        self.assertTrue(policy.is_active)
        self.assertEqual(policy.credits_per_gb, Decimal("12.50"))
        self.assertEqual(policy.included_gb, Decimal("1.000"))
        rows = {row["company"].pk: row for row in response.context["live_storage_rows"]}
        self.assertEqual(rows[self.company.pk]["billable_gb"], Decimal("1.000"))
        self.assertEqual(rows[self.company.pk]["estimated_credits"], Decimal("12.50"))
        self.assertEqual(rows[self.other.pk]["estimated_credits"], Decimal("0.00"))
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("40.00"))
        self.assertEqual(CreditLedger.objects.count(), 0)

        first, _ = charge_storage_period(self.company)
        second, _ = charge_storage_period(self.company)
        self.assertEqual(first, "charged")
        self.assertEqual(second, "already_billed")
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("27.50"))
        self.assertEqual(CreditLedger.objects.filter(kind="storage_charge", company=self.company).count(), 1)
        page = self.client.get(self.url)
        rows = {row["company"].pk: row for row in page.context["live_storage_rows"]}
        self.assertTrue(rows[self.company.pk]["already_billed"])

    def test_invalid_rate_rejected_without_mutating_previous_policy(self):
        policy = StorageCreditPolicy.objects.create(
            credits_per_gb=Decimal("5.00"), included_gb=Decimal("1"),
            billing_period="daily", is_active=False,
        )
        result = self.client.post(self.url, {
            "credits_per_gb": "0.00",
            "included_gb": "-1",
            "billing_period": "daily",
            "is_active": "on",
        })
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Set a rate above zero")
        policy.refresh_from_db()
        self.assertFalse(policy.is_active)
        self.assertEqual(policy.credits_per_gb, Decimal("5.00"))
        self.assertFalse(CreditLedger.objects.exists())

    def test_read_only_platform_role_cannot_edit_pricing(self):
        readonly_role = Role.objects.create(
            name="Storage Viewer", code="STORAGE_VIEWER", company=None,
            permissions=["panel.storage.view"],
        )
        viewer = User.objects.create_user(
            email="storage-viewer@example.test", name="Viewer",
            password="StrongStorageTest123!", role=readonly_role, is_staff=True,
        )
        self.client.force_login(viewer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Save Storage Pricing")
        forbidden = self.client.post(self.url, {
            "credits_per_gb": "100", "included_gb": "0",
            "billing_period": "daily", "is_active": "on",
        })
        self.assertEqual(forbidden.status_code, 403)
        self.assertFalse(StorageCreditPolicy.objects.exists())

    def test_tenant_role_cannot_view_platform_storage(self):
        role = Role.objects.create(
            name="Tenant Owner", code="OWNER", company=self.company,
            permissions=["*", "storage.view"],
        )
        tenant = User.objects.create_user(
            email="tenant-storage@example.test", name="Tenant",
            password="StrongStorageTest123!", company=self.company, role=role,
        )
        self.client.force_login(tenant)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url).status_code, 403)

    def test_company_filter_does_not_include_other_company_usage(self):
        response = self.client.get(self.url, {"company": str(self.company.pk)})
        self.assertEqual(response.status_code, 200)
        rows = response.context["live_storage_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"].pk, self.company.pk)
        self.assertEqual(rows[0]["used_gb"], Decimal("2.000"))
