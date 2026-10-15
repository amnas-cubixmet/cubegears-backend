"""Auto-discovered paid features, active-user metering and storage wallet safety."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.feature_discovery import module_inventory, sync_existing_modules
from apps.saas.models import FeatureGroup, MediaFile, PlanFeature
from apps.credits.models import (
    CompanyCreditFeature, CreditLedger, CreditWallet, FeatureBundle,
    FeatureCreditPrice, StorageCreditPolicy,
)
from apps.credits.service import enable_paid_feature, renew_feature, purchase_feature_bundle
from apps.credits.storage_billing import charge_storage_period, storage_quote


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=True)
class MeteredCreditsTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Metered Garage", slug="metered-garage")
        self.other = Company.objects.create(name="Separate Garage", slug="separate-garage")
        role = Role.objects.create(
            company=self.company, name="Workshop Admin", code="ADMIN",
            permissions=["jobs.view", "billing.view", "billing.edit"],
        )
        self.admin = User.objects.create_user(
            email="metered-owner@example.test", name="Owner", company=self.company,
            role=role, password="SafeTestPassword123!",
        )
        self.second = User.objects.create_user(
            email="metered-user@example.test", name="Employee", company=self.company,
            role=role, password="SafeTestPassword123!",
        )
        self.inactive = User.objects.create_user(
            email="inactive-user@example.test", name="Inactive", company=self.company,
            role=role, password="SafeTestPassword123!", is_active=False,
        )
        self.group = FeatureGroup.objects.create(key="billing-test", label="Operations")
        self.feature = PlanFeature.objects.create(key="jobs", label="Job Cards", group=self.group)
        CreditWallet.objects.create(company=self.company, balance=Decimal("100.00"))

    def test_registry_discovers_real_modules_and_preserves_admin_customizations(self):
        discovered = {m["key"]: m for m in module_inventory()}
        self.assertIn("jobs", discovered)
        self.assertIn("payroll", discovered)
        self.assertIn("storage", discovered)
        self.assertFalse(discovered["storage"]["billable"])
        self.assertTrue(discovered["jobs"]["billable"])
        self.assertTrue(all(not m["key"].startswith("panel") for m in discovered.values()))
        created = sync_existing_modules()
        self.assertIn("vehicles", created)
        self.assertNotIn("jobs", created)
        self.feature.label = "Custom Workshop Jobs"
        self.feature.save(update_fields=["label"])
        self.assertEqual(sync_existing_modules(), [])
        self.feature.refresh_from_db()
        self.assertEqual(self.feature.label, "Custom Workshop Jobs")
        self.assertFalse(FeatureCreditPrice.objects.exists())

    def test_individual_daily_activation_and_user_based_recurring_debits(self):
        rate = FeatureCreditPrice.objects.create(
            feature=self.feature, base_credits=Decimal("5.00"),
            activation_credits=Decimal("3.00"),
            per_user_credits=Decimal("2.00"),
            billing_period="daily", is_available=True,
        )
        self.assertFalse(user_has_permission(self.admin, "jobs.view"))
        state, price = enable_paid_feature(self.company, self.feature, self.admin)
        self.assertEqual(price, Decimal("12.00"))  # 5 + (2 users × 2) + 3
        self.assertTrue(user_has_permission(self.admin, "jobs.view"))
        self.assertTrue(user_has_permission(self.second, "jobs.view"))
        _, charged_again = enable_paid_feature(self.company, self.feature, self.admin)
        self.assertEqual(charged_again, Decimal("0.00"))
        self.assertEqual(CreditLedger.objects.filter(company=self.company, kind="feature_charge").count(), 1)
        state.next_charge_at = timezone.now() - timedelta(seconds=1)
        state.save(update_fields=["next_charge_at"])
        self.assertFalse(user_has_permission(self.admin, "jobs.view"))
        self.assertEqual(renew_feature(state), "renewed")
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("79.00"))
        self.assertEqual(CreditLedger.objects.filter(company=self.company, kind="feature_charge").count(), 2)
        self.assertTrue(user_has_permission(self.second, "jobs.view"))
        self.assertEqual(rate.billing_period, "daily")

    def test_bundle_has_seat_price_and_activation_fee_without_double_charging(self):
        other_feature = PlanFeature.objects.create(key="stock", label="Stock", group=self.group)
        bundle = FeatureBundle.objects.create(
            key="workshop-bundle", name="Workshop", price_credits=Decimal("10.00"),
            activation_credits=Decimal("5.00"), per_user_credits=Decimal("3.00"),
            billing_type="monthly",
        )
        bundle.features.set([self.feature, other_feature])
        access, charged = purchase_feature_bundle(self.company, bundle, self.admin)
        self.assertEqual(charged, Decimal("21.00"))  # 10 + 2×3 + 5
        self.assertGreater(access.valid_until, timezone.now() + timedelta(days=29))
        _, extension = purchase_feature_bundle(self.company, bundle, self.admin)
        self.assertEqual(extension, Decimal("16.00"))  # no second activation fee
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("63.00"))

    def test_storage_actual_bytes_and_one_charge_per_calendar_period(self):
        policy = StorageCreditPolicy.objects.create(
            billing_period="monthly", included_gb=Decimal("1"),
            credits_per_gb=Decimal("4"), is_active=True,
        )
        MediaFile.objects.create(
            company=self.company, file="saas/media.mp4", name="Real Upload",
            size_bytes=2 * (1024 ** 3),
        )
        quote = storage_quote(self.company, policy)
        self.assertEqual(quote["used_gb"], Decimal("2.000"))
        self.assertEqual(quote["credits"], Decimal("4.00"))
        result, _ = charge_storage_period(self.company, policy)
        self.assertEqual(result, "charged")
        repeated, _ = charge_storage_period(self.company, policy)
        self.assertEqual(repeated, "already_billed")
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("96.00"))
        self.assertEqual(CreditLedger.objects.filter(kind="storage_charge", company=self.company).count(), 1)
        self.assertFalse(CreditLedger.objects.filter(company=self.other).exists())

    def test_storage_insufficient_balance_does_not_go_negative(self):
        policy = StorageCreditPolicy.objects.create(
            billing_period="daily", included_gb=Decimal("0"),
            credits_per_gb=Decimal("900"), is_active=True,
        )
        MediaFile.objects.create(company=self.company, file="saas/large.bin", name="Large", size_bytes=1024 ** 3)
        result, _ = charge_storage_period(self.company, policy)
        self.assertEqual(result, "insufficient_credits")
        self.assertFalse(CreditLedger.objects.filter(kind="storage_charge").exists())
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal("100.00"))

    def test_platform_sync_endpoint_is_idempotent_and_requires_platform_admin(self):
        owner = User.objects.create_superuser(
            email="platform-metered@example.test", name="Platform",
            password="SafeTestPassword123!",
        )
        self.client.force_login(owner)
        url = reverse("panel:features-edit")
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(PlanFeature.objects.filter(key="vehicles").count(), 1)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url).status_code, 403)
