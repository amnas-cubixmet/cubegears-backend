"""Persist human-readable, company-scoped Job Card audit events.

Only call after a successful mutation, preferably in the same transaction.
Do not record values that would expose employee/private financial information.
"""
from .models import JobActivity


def record_job_event(job, actor, event, description="", metadata=None):
    return JobActivity.objects.create(
        company=job.company,
        branch=job.branch,
        job=job,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        event=str(event)[:80],
        description=str(description or "")[:2000],
        metadata=metadata or {},
    )
