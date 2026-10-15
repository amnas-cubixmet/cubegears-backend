"""Removing the discovery UI must not delete paid labels, entitlements or permissions."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.credits.models import FeatureBundle
from apps.saas.models import FeatureGroup, PlanFeature


class PaidFeatureCatalogOnlyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="feature-cleanup@example.test",
            name="Feature Cleanup Administrator",
            password="FeatureCleanupSecurePass123!",
        )
        cls.discovered_group = FeatureGroup.objects.create(
            key="discovered-modules", label="Discovered Workshop Modules",
        )
        cls.jobs = PlanFeature.objects.create(
            group=cls.discovered_group, key="jobs", label="Job Cards",
        )
        cls.stock = PlanFeature.objects.create(
            group=cls.discovered_group, key="stock", label="Stock & Inventory",
        )
        cls.bundle = FeatureBundle.objects.create(
            key="workshop-pro",
            name="Workshop Pro",
            price_credits=Decimal("50.00"),
            activation_credits=Decimal("10.00"),
            per_user_credits=Decimal("5.00"),
            billing_type="monthly",
            is_active=True,
        )
        cls.bundle.features.set([cls.jobs, cls.stock])

    def setUp(self):
        self.client.force_login(self.admin)

    def test_discovery_panels_hidden_paid_bundle_and_price_still_present(self):
        response = self.client.get(reverse("panel:features"))
        self.assertEqual(response.status_code, 200)
        for excluded in (
            "Discovered Module Inventory",
            "Sync Missing Modules",
            "Discovered Workshop Modules",
            "Module Groups",
            "Backend permission key",
            "Feature catalog overview",
        ):
            self.assertNotContains(response, excluded)
        self.assertContains(response, "Paid Feature Labels")
        self.assertContains(response, "Workshop Pro")
        self.assertContains(response, "50.00")
        self.assertContains(response, "Job Cards")
        self.assertContains(response, "Stock &amp; Inventory")
        self.assertContains(response, "Edit Package")

    def test_page_view_does_not_automatically_sync_modules(self):
        response = self.client.get(reverse("panel:features"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Sync Missing Modules")
        self.assertEqual(FeatureGroup.objects.count(), 1)
        self.assertEqual(PlanFeature.objects.count(), 2)
        # Preserve the existing privileged sync API for historical integrations,
        # but do not expose it as an action on the feature-pricing page.
        self.assertEqual(self.client.get(reverse("panel:features-edit")).status_code, 405)

    def test_existing_pricing_and_feature_permissions_are_untouched(self):
        self.client.get(reverse("panel:features"))
        self.jobs.refresh_from_db()
        self.bundle.refresh_from_db()
        self.assertEqual(self.jobs.key, "jobs")
        self.assertEqual(self.bundle.price_credits, Decimal("50.00"))
        self.assertEqual(self.bundle.features.count(), 2)
        edit_url = reverse("panel:featurebundle-edit", args=[self.bundle.pk])
        response = self.client.get(edit_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Job Cards")
