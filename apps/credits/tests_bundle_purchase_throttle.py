"""Guardrail for company module activation while retaining a purchase rate limit."""
from django.test import SimpleTestCase

from apps.credits.bundle_views import BundlePurchaseThrottle, BundlePurchaseView


class BundlePurchaseThrottleTests(SimpleTestCase):
    def test_owner_can_activate_full_module_catalog_without_unlimited_writes(self):
        throttle = BundlePurchaseThrottle()
        self.assertEqual(throttle.num_requests, 30)
        self.assertEqual(throttle.duration, 60 * 60)
        self.assertIn(BundlePurchaseThrottle, BundlePurchaseView.throttle_classes)
