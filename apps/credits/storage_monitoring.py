"""Read-only, configurable storage usage snapshots.

The task never changes wallets, billing periods or credit ledger records.
The daily history row is refreshed at each due checkpoint, using real MediaFile
metadata. Observations between checkpoints are not invented.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.companies.models import Company
from apps.saas.models import MediaFile, StorageUsage
from .models import StorageMonitoringPolicy, StorageMonitoringReading

logger = logging.getLogger(__name__)
VALID_INTERVALS = (3600, 7200, 21600, 43200, 86400)


@transaction.atomic
def snapshot_due_storage(*, at=None, force=False):
    """Take one daily snapshot per active company if the configured tick is due.

    An atomic, locked policy row coordinates concurrent Beat workers. A second
    tick in the same day updates the same monitoring row, preserving one
    meaningful daily history entry with the latest observation timestamp.
    """
    at = at or timezone.now()
    if timezone.is_naive(at):
        raise ValueError("Storage snapshot timestamp must be timezone-aware.")
    if at > timezone.now() + timedelta(minutes=1):
        raise ValueError("Future storage snapshots are not allowed.")

    policy = StorageMonitoringPolicy.objects.select_for_update().filter(singleton=1).first()
    if policy is None or not policy.enabled:
        return {"status": "disabled", "companies": 0}
    if policy.interval_seconds not in VALID_INTERVALS:
        return {"status": "invalid_interval", "companies": 0}
    if (not force and policy.last_run_at is not None
            and at < policy.last_run_at + timedelta(seconds=policy.interval_seconds)):
        return {"status": "not_due", "companies": 0}

    counts = {
        row["company_id"]: row
        for row in MediaFile.objects.filter(company__is_active=True)
        .values("company_id")
        .annotate(bytes=Sum("size_bytes"), files=Count("pk"))
    }
    date = timezone.localdate(at)
    written = 0
    for company_id in Company.objects.filter(is_active=True).values_list("pk", flat=True).iterator():
        row = counts.get(company_id) or {}
        values = {
            "bytes_used": max(0, int(row.get("bytes") or 0)),
            "file_count": max(0, int(row.get("files") or 0)),
            "metadata": {"sampled_at": at.isoformat(), "source": "automatic_storage_monitor"},
        }
        # The legacy StorageUsage model is date-granular and lacks a
        # uniqueness constraint, so update a pre-existing monitoring row.
        existing = StorageUsage.objects.filter(
            company_id=company_id, date=date, category="auto_monitor",
        ).order_by("-pk").first()
        if existing:
            StorageUsage.objects.filter(pk=existing.pk).update(**values)
        else:
            StorageUsage.objects.create(
                company_id=company_id, date=date, category="auto_monitor",
                **values,
            )
        # A separate timestamped reading preserves every actual checkpoint.
        # Updating the legacy daily row must not erase within-day observations.
        StorageMonitoringReading.objects.get_or_create(
            company_id=company_id, sampled_at=at,
            defaults={
                "interval_seconds": policy.interval_seconds,
                "bytes_used": values["bytes_used"],
                "file_count": values["file_count"],
            },
        )
        written += 1

    policy.last_run_at = at
    policy.save(update_fields=["last_run_at", "updated_at"])
    return {"status": "sampled", "companies": written, "at": at.isoformat()}
