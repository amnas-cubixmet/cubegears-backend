"""Regression tests for hour-based storage settings and durable observations."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.companies.models import Company
from apps.credits.models import (
    StorageCreditPolicy, StorageMonitoringPolicy, StorageMonitoringReading,
)
from apps.credits.storage_forms import StorageCreditPolicyForm
from apps.credits.storage_interval_logs import interval_log_page
from apps.credits.storage_monitoring import snapshot_due_storage


class StorageHourFormTests(TestCase):
    def test_hours_are_converted_to_existing_seconds_column(self):
        policy = StorageCreditPolicy(meter_interval_seconds=3600)
        form = StorageCreditPolicyForm({
            "credits_per_gb": "0.20",
            "included_gb": "0.000",
            "billing_mode": "snapshot",
            "billing_period": "monthly",
            "meter_interval_hours": "2.5",
        }, instance=policy)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).meter_interval_seconds, 9000)

    def test_old_seconds_only_posts_preserve_existing_cadence(self):
        policy = StorageCreditPolicy(meter_interval_seconds=7200)
        form = StorageCreditPolicyForm({
            "credits_per_gb": "0.20",
            "included_gb": "0",
            "billing_mode": "snapshot",
            "billing_period": "monthly",
        }, instance=policy)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).meter_interval_seconds, 7200)

    def test_out_of_range_hours_cannot_be_saved(self):
        form = StorageCreditPolicyForm({
            "credits_per_gb": "0.20",
            "included_gb": "0",
            "billing_mode": "snapshot",
            "billing_period": "monthly",
            "meter_interval_hours": "25",
        }, instance=StorageCreditPolicy())
        self.assertFalse(form.is_valid())
        self.assertIn("meter_interval_hours", form.errors)


class StorageIntervalMonitorTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Workshop", slug="test-workshop")
        self.other = Company.objects.create(name="Other Workshop", slug="other-workshop")
        StorageMonitoringPolicy.objects.create(
            singleton=1, enabled=True, interval_seconds=3600,
        )

    def test_real_hourly_checkpoints_are_immutable_and_company_scoped(self):
        start = timezone.now() - timedelta(hours=3)
        self.assertEqual(snapshot_due_storage(at=start)["status"], "sampled")
        self.assertEqual(snapshot_due_storage(at=start + timedelta(minutes=20))["status"], "not_due")
        self.assertEqual(snapshot_due_storage(at=start + timedelta(hours=1))["status"], "sampled")
        rows = StorageMonitoringReading.objects.filter(company=self.company)
        self.assertEqual(rows.count(), 2)
        self.assertTrue(all(row.interval_seconds == 3600 for row in rows))
        self.assertEqual(interval_log_page(self.company)["count"], 2)
        self.assertEqual(interval_log_page(self.other)["count"], 2)
        self.assertTrue(all(row["source"] == "monitoring" for row in interval_log_page(self.company)["results"]))
        # Replaying the identical forced checkpoint must not create a duplicate.
        snapshot_due_storage(at=start + timedelta(hours=1), force=True)
        self.assertEqual(rows.count(), 2)

    def test_date_window_and_company_filter(self):
        now = timezone.now() - timedelta(hours=1)
        snapshot_due_storage(at=now)
        earlier = (timezone.localdate(now) - timedelta(days=1)).isoformat()
        result = interval_log_page(self.company, from_date=earlier, limit=1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["accruedCredits"], None)
