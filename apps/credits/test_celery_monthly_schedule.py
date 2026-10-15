"""Regression checks for opt-in monthly metered Celery Beat registration."""
import ast
from pathlib import Path
from django.test import SimpleTestCase


class MonthlyCeleryScheduleTests(SimpleTestCase):
    def test_monthly_jobs_registered_with_expected_tasks(self):
        settings_text = (Path(__file__).resolve().parents[2] / "config" / "settings.py").read_text(encoding="utf-8")
        tasks_text = (Path(__file__).resolve().parent / "tasks.py").read_text(encoding="utf-8")
        for task in (
            "cubegear.features.monthly.sample",
            "cubegear.features.monthly.settle",
            "cubegear.storage.monthly.sample",
            "cubegear.storage.monthly.settle",
        ):
            self.assertIn(f'"task": "{task}"', settings_text)
            self.assertIn(f'name="{task}"', tasks_text)

    def test_monthly_jobs_guarded_by_flags_and_old_jobs_removed(self):
        settings_text = (Path(__file__).resolve().parents[2] / "config" / "settings.py").read_text(encoding="utf-8")
        self.assertIn('if CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED:', settings_text)
        self.assertIn('if STORAGE_MONTHLY_METERED_ENABLED:', settings_text)
        for legacy in (
            "cubegear-renew-credit-features",
            "cubegear-sync-postpaid-seat-rates",
            "cubegear-monthly-usage-settlement",
            "cubegear-bill-storage-period",
            "cubegear-meter-storage",
        ):
            self.assertIn(f'CELERY_BEAT_SCHEDULE.pop("{legacy}", None)', settings_text)
        ast.parse(settings_text)
