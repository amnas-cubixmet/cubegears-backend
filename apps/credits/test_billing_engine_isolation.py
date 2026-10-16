"""All historical cycle engines remain in schema but must not charge together."""
from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase, override_settings

from .models import BillingCyclePolicy, CreditCyclePolicy, MonthlyBillingPolicy
from .calendar_billing import active_policy
from .cycle_billing import effective_policy
from .monthly_policy import policy as older_monthly_policy
from .billing_cycle_forms import BillingCyclePolicyForm


class BillingEngineIsolationTests(TestCase):
    def test_migration_graph_has_only_one_leaf(self):
        loader = MigrationLoader(connection)
        # New billing migrations are valid if there is still exactly one leaf.
        leaves = loader.graph.leaf_nodes("credits")
        self.assertEqual(len(leaves), 1, leaves)

    def test_cycle_engines_refuse_overlapping_active_switches(self):
        calendar = BillingCyclePolicy.objects.create(enabled=True, due_day=5, effective_month="2026-10")
        self.assertIsNotNone(active_policy())
        CreditCyclePolicy.objects.create(enabled=True, day_of_month=5)
        # Existing conflicting records must cause both engines to fail closed.
        self.assertIsNone(active_policy())
        self.assertIsNone(effective_policy())
        calendar.enabled = False
        calendar.save(update_fields=["enabled"])
        self.assertIsNotNone(effective_policy())

    def test_old_monthly_engine_cannot_start_if_calendar_enabled(self):
        BillingCyclePolicy.objects.create(enabled=True, due_day=5)
        MonthlyBillingPolicy.objects.create(pk=1, enabled=True, due_day=5)
        self.assertIsNone(active_policy())
        self.assertIsNone(older_monthly_policy())

    @override_settings(
        CREDIT_BILLING_CELERY_ENABLED=True,
        STORAGE_MONTHLY_METERED_ENABLED=True,
        CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=False,
        CREDIT_POSTPAID_FEATURE_BILLING_ENABLED=False,
    )
    def test_admin_form_rejects_older_cycle_policy(self):
        CreditCyclePolicy.objects.create(enabled=True, day_of_month=5)
        form = BillingCyclePolicyForm(data={
            "enabled": "on", "due_day": 5, "restrict_overdue": "on",
            "allow_zero_balance_usage": "on",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("5th-to-5th billing cycle", str(form.errors))

    def test_legacy_credit_and_storage_commands_noop_in_calendar_mode(self):
        BillingCyclePolicy.objects.create(enabled=True, due_day=5)
        stdout = StringIO()
        call_command("charge_credit_features", stdout=stdout)
        self.assertIn("no legacy debit", stdout.getvalue())
        stdout = StringIO()
        call_command("bill_storage_credits", stdout=stdout)
        self.assertIn("no legacy debit", stdout.getvalue())
