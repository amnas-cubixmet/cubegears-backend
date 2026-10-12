"""Automatic customer complaint status based on confirmed Job Card workflow.

Complaint rows are stored as JSON for backward compatibility. Their status
is controlled by workflow progress, not independently edited by operators.
"""
from .workflow import workflow_state


def complaint_status_for_job(job):
    completed = set(workflow_state(job)["completed"])
    if "qc" in completed:
        return "Completed"
    if "estimate" in completed:
        return "In Progress"
    return "Open"


def normalize_complaints(complaints, status):
    """Keep legacy complaint contents/IDs while normalizing the status."""
    if not isinstance(complaints, list):
        return complaints
    return [
        {**item, "status": status} if isinstance(item, dict) else item
        for item in complaints
    ]


def sync_complaint_status(job):
    """Update persisted rows on stage transitions; return whether it changed."""
    normalized = normalize_complaints(job.complaints, complaint_status_for_job(job))
    if normalized == job.complaints:
        return False
    job.complaints = normalized
    return True
