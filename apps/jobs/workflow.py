"""Canonical sequential Job Card stages used by the API and company-panel.

The status fallback preserves existing, in-flight jobs. Once an explicit stage
has been completed, progress is stored in Job.workflow_progress.
"""
STAGES = ("overview", "inspection", "estimate", "work", "qc", "invoice")
STATUS_INDEX = {
    "New": 0,
    "Inspection": 1,
    "Estimate Pending": 2,
    "Approved": 3,
    "In Progress": 3,
    "Waiting for Parts": 3,
    "QC": 4,
    "Ready for Delivery": 5,
    "Delivered": 6,
}
NEXT_STATUS = {
    "overview": "Inspection",
    "inspection": "Estimate Pending",
    "estimate": "In Progress",
    "work": "QC",
    "qc": "Ready for Delivery",
    "invoice": "Delivered",
}


def stage_index_from_status(status):
    return STATUS_INDEX.get(status, 0)


def workflow_state(job):
    saved = job.workflow_progress or {}
    completed = set(saved.get("completed") or [])
    # A job already at a later workshop status should not lose historical work.
    # Otherwise saved completion markers are the source of truth.
    status_index = stage_index_from_status(job.status)
    completed.update(STAGES[:min(status_index, len(STAGES))])
    current = next((stage for stage in STAGES if stage not in completed), STAGES[-1])
    position = STAGES.index(current)
    return {
        "current": current,
        "completed": [stage for stage in STAGES if stage in completed],
        "locked": list(STAGES[position + 1:]),
        "stages": list(STAGES),
        "finished": len(completed) == len(STAGES),
    }
