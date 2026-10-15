"""Celery adapter must not change balances unless explicitly enabled."""
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings


class CreditCeleryTaskSafetyTests(SimpleTestCase):
    @override_settings(CREDIT_BILLING_CELERY_ENABLED=False)
    @patch("apps.credits.tasks.call_command")
    def test_all_scheduled_wallet_tasks_disabled_by_default(self, cmd):
        from apps.credits.tasks import bill_due_storage, renew_due_features
        self.assertEqual(bill_due_storage(), "disabled")
        self.assertEqual(renew_due_features(), "disabled")
        cmd.assert_not_called()

    @override_settings(CREDIT_BILLING_CELERY_ENABLED=True)
    @patch("apps.credits.tasks.call_command")
    def test_enabled_tasks_use_existing_audited_commands(self, cmd):
        from apps.credits.tasks import bill_due_storage, renew_due_features
        self.assertEqual(bill_due_storage(), "completed")
        self.assertEqual(renew_due_features(), "completed")
        self.assertEqual(cmd.call_args_list[0].args, ("bill_storage_credits",))
        self.assertEqual(cmd.call_args_list[1].args, ("charge_credit_features",))
