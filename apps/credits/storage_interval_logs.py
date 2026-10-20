"""Bounded, company-scoped read-only storage interval audit records.

Monitoring checkpoints never debit a wallet. Billing meter rows expose only
already-persisted accrued credits; no estimated charge is invented here.
"""
from apps.credits.models import (
    StorageMeterReading, StorageMonitoringReading, StorageUsageInterval,
)


def interval_log_page(company, *, from_date="", to_date="", limit=50, offset=0):
    """Return a merged newest-first page without loading unlimited history."""
    window = offset + limit
    result = []
    counts = []

    def filtered(model, field):
        qs = model.objects.filter(company=company)
        if from_date:
            qs = qs.filter(**{field + "__date__gte": from_date})
        if to_date:
            qs = qs.filter(**{field + "__date__lte": to_date})
        counts.append(qs.count())
        return qs.order_by("-" + field, "-pk")[:window]

    for reading in filtered(StorageMonitoringReading, "sampled_at"):
        result.append({
            "id": "monitor-" + str(reading.pk),
            "source": "monitoring",
            "sampledAt": reading.sampled_at.isoformat(),
            "usedBytes": reading.bytes_used,
            "usedGb": round(reading.bytes_used / (1024 ** 3), 4),
            "fileCount": reading.file_count,
            "intervalSeconds": reading.interval_seconds,
            "coveredSeconds": None,
            "accruedCredits": None,
            "_timestamp": reading.sampled_at,
        })
    for reading in filtered(StorageMeterReading, "sampled_at"):
        result.append({
            "id": "monthly-meter-" + str(reading.pk),
            "source": "monthly_meter",
            "sampledAt": reading.sampled_at.isoformat(),
            "usedBytes": max(0, reading.used_bytes),
            "usedGb": round(max(0, reading.used_bytes) / (1024 ** 3), 4),
            "fileCount": None,
            "intervalSeconds": None,
            "coveredSeconds": reading.covered_seconds,
            "accruedCredits": str(reading.accrued_credits),
            "_timestamp": reading.sampled_at,
        })
    for reading in filtered(StorageUsageInterval, "ends_at"):
        result.append({
            "id": "legacy-meter-" + str(reading.pk),
            "source": "legacy_meter",
            "sampledAt": reading.ends_at.isoformat(),
            "startsAt": reading.starts_at.isoformat(),
            "usedBytes": reading.measured_bytes,
            "usedGb": round(reading.measured_bytes / (1024 ** 3), 4),
            "fileCount": None,
            "intervalSeconds": None,
            "coveredSeconds": int((reading.ends_at - reading.starts_at).total_seconds()),
            "accruedCredits": str(reading.credits_accrued),
            "_timestamp": reading.ends_at,
        })
    result.sort(key=lambda row: (row["_timestamp"], row["id"]), reverse=True)
    page = result[offset:window]
    for row in page:
        row.pop("_timestamp")
    return {"count": sum(counts), "results": page}
