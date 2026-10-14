"""Server-owned, company-scoped storage history for SaaS MediaFile records."""
from django.db.models import Sum, Count
from django.utils import timezone
from .models import MediaFile, StorageEvent

def ensure_baseline(company):
    if StorageEvent.objects.filter(company=company).exists():
        return
    total = 0
    for count, media in enumerate(MediaFile.objects.filter(company=company).order_by("created_at","id"), 1):
        before = total
        total += max(0, media.size_bytes)
        StorageEvent.objects.create(
            company=company, branch=media.branch, event_type="BASELINE",
            file_id_snapshot=media.id, name_snapshot=media.name, category_snapshot=media.category,
            mime_type_snapshot=media.mime_type, file_size_bytes=media.size_bytes,
            delta_bytes=media.size_bytes, storage_before_bytes=before, storage_after_bytes=total,
            file_count_after=count, actor=media.uploaded_by,
        )

def record_event(media, kind, actor):
    if kind not in ("UPLOAD", "DELETE"):
        raise ValueError("Invalid storage event")
    state=MediaFile.objects.filter(company=media.company).aggregate(total=Sum("size_bytes"), count=Count("id"))
    after=int(state["total"] or 0)
    delta=media.size_bytes if kind=="UPLOAD" else -media.size_bytes
    return StorageEvent.objects.create(
        company=media.company, branch=media.branch, event_type=kind,
        file_id_snapshot=media.id, name_snapshot=media.name, category_snapshot=media.category,
        mime_type_snapshot=media.mime_type, file_size_bytes=media.size_bytes,
        delta_bytes=delta, storage_before_bytes=after-delta, storage_after_bytes=after,
        file_count_after=state["count"], actor=actor,
    )

def as_dict(event):
    return {"id":str(event.id),"eventType":event.event_type,"fileId":str(event.file_id_snapshot),
        "filename":event.name_snapshot,"category":event.category_snapshot,
        "mimeType":event.mime_type_snapshot,"sizeBytes":event.file_size_bytes,
        "deltaBytes":event.delta_bytes,"storageBeforeBytes":event.storage_before_bytes,
        "storageAfterBytes":event.storage_after_bytes,"fileCountAfter":event.file_count_after,
        "actor":event.actor.name if event.actor_id and event.actor else None,
        "occurredAt":event.occurred_at.isoformat()}


def history_days(company, start=None, end=None):
    """Actual event days, no invented usage or charges."""
    qs = StorageEvent.objects.filter(company=company).order_by("occurred_at", "created_at", "id")
    if start:
        qs = qs.filter(occurred_at__date__gte=start)
    if end:
        qs = qs.filter(occurred_at__date__lte=end)
    days = {}
    for event in qs:
        day = timezone.localdate(event.occurred_at).isoformat()
        days[day] = {
            "date": day,
            "usedGb": round(event.storage_after_bytes / (1024 ** 3), 3),
            "fileCount": event.file_count_after,
            "rate": None,
            "charge": None,
            "snapshotType": "event-ledger",
        }
    return sorted(days.values(), key=lambda item: item["date"], reverse=True)


def history_day(company, day):
    """Rebuild the files at close of the requested event day."""
    qs = StorageEvent.objects.filter(
        company=company, occurred_at__date__lte=day
    ).select_related("actor").order_by("occurred_at", "created_at", "id")
    inventory = {}
    selected = []
    last = None
    for event in qs:
        last = event
        file_id = str(event.file_id_snapshot)
        if event.event_type == StorageEvent.DELETE:
            inventory.pop(file_id, None)
        else:
            inventory[file_id] = {
                "id": file_id,
                "name": event.name_snapshot,
                "category": event.category_snapshot,
                "sizeMb": round(event.file_size_bytes / (1024 ** 2), 2),
                "uploadedBy": event.actor.name if event.actor_id and event.actor else None,
                "uploadedAt": event.occurred_at.isoformat(),
                "type": event.mime_type_snapshot,
            }
        if timezone.localdate(event.occurred_at) == day:
            selected.append(as_dict(event))
    if not selected:
        return None
    return {
        "date": day.isoformat(),
        "usedGb": round(last.storage_after_bytes / (1024 ** 3), 3),
        "fileCount": last.file_count_after,
        "rate": None,
        "charge": None,
        "snapshotType": "event-ledger",
        "files": list(inventory.values()),
        "events": list(reversed(selected)),
    }
