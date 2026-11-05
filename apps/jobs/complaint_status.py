"""Independently reviewed customer complaint progress.

Job stages never resolve a complaint. Historical "Completed" values were
auto-derived from QC in older installations, so they must be re-reviewed rather
than misrepresented as a technician-confirmed resolution.
"""
VALID_COMPLAINT_STATUSES = (
    "Pending", "In Progress", "Resolved", "Not Resolved", "Not Applicable",
)


def manual_complaint_status(entry):
    raw = str((entry or {}).get("status") or "").strip()
    if raw in VALID_COMPLAINT_STATUSES:
        return raw
    # Former auto statuses ("Open", "Completed") are not proof of resolution.
    return "Pending"


def complaint_status_for_job(job):
    """Compatibility helper for old clients; never infer resolution from stage."""
    return "Pending"


def normalize_complaints(complaints, status=None):
    if not isinstance(complaints, list):
        return complaints
    return [
        {**item, "status": manual_complaint_status(item)}
        if isinstance(item, dict) else item
        for item in complaints
    ]


def unresolved_complaints(job):
    return [
        item for item in normalize_complaints(job.complaints)
        if isinstance(item, dict) and item.get("status") not in
        {"Resolved", "Not Applicable"}
    ]


def sync_complaint_status(job):
    """Legacy workflow hook: intentionally no automatic status transitions."""
    return False
