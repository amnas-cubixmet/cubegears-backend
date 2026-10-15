"""Catalog discovery only advertises existing permission-backed functions."""
import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.roles.permission_catalog import TENANT_PERMISSIONS
from apps.saas.management.commands.audit_feature_catalog import discover_modules
from apps.saas.models import FeatureGroup, PlanFeature


class CatalogAuditTests(TestCase):
    def test_only_backend_permission_modules_are_discovered(self):
        discovered = discover_modules()
        self.assertIn("jobs", discovered)
        self.assertIn("staff", discovered)
        self.assertIn("storage", discovered)
        self.assertFalse(discovered["storage"]["priceable"])
        self.assertFalse(discovered["billing"]["priceable"])
        self.assertFalse(discovered["attendance"]["priceable"])
        for key in discovered:
            self.assertTrue(
                any(p.startswith(key + ".") for p in TENANT_PERMISSIONS)
                or discovered[key]["modelBacked"]
            )

    def test_default_audit_does_not_modify_catalog_or_wallet(self):
        out = StringIO()
        call_command("audit_feature_catalog", stdout=out)
        result = json.loads(out.getvalue())
        self.assertFalse(result["syncRequested"])
        self.assertEqual(PlanFeature.objects.count(), 0)
        self.assertEqual(FeatureGroup.objects.count(), 0)

    def test_sync_registers_only_missing_disabled_modules_idempotently(self):
        out = StringIO()
        call_command("audit_feature_catalog", "--sync", stdout=out)
        first = PlanFeature.objects.count()
        self.assertGreater(first, 0)
        self.assertFalse(PlanFeature.objects.filter(is_active=True).exists())
        self.assertFalse(PlanFeature.objects.filter(key="storage").exists())
        self.assertFalse(PlanFeature.objects.filter(key="billing").exists())
        call_command("audit_feature_catalog", "--sync", stdout=StringIO())
        self.assertEqual(PlanFeature.objects.count(), first)
        self.assertEqual(FeatureGroup.objects.count(), 1)

    def test_sync_preserves_existing_admin_feature_metadata(self):
        group = FeatureGroup.objects.create(key="original", label="Original")
        row = PlanFeature.objects.create(
            key="jobs", label="My Custom Label", group=group, is_active=True,
        )
        call_command("audit_feature_catalog", "--sync", stdout=StringIO())
        row.refresh_from_db()
        self.assertEqual(row.label, "My Custom Label")
        self.assertEqual(row.group_id, group.id)
        self.assertTrue(row.is_active)
