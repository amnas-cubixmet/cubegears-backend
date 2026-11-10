"""Private, permission-scoped staff notifications for workshop job events.

Called only after successful Job Card writes, in the same database transaction.
The event receipt prevents duplicate alerts when the same event is retried.
"""
from apps.accounts.models import User
from apps.saas.models import CompanySetting

from .models import Notification

EVENT_SETTINGS = {
    "job_created": "jobCreated",
    "job_assigned": "jobAssigned",
    "job_status_changed": "jobStatus",
    "job_ready": "jobReady",
}


def notify_job_event(job, kind, activity):
    """Emit one private inbox row per eligible staff member, never user=NULL.

    A company's other branches, users without inbox/job access, and inactive
    accounts must not receive job details. The owner is included as a workshop
    administrator, as in the existing customer-estimate response flow.
    """
    if kind not in EVENT_SETTINGS:
        raise ValueError("Unsupported workshop notification type.")
    if not job.company_id or activity is None:
        return 0

    overrides = CompanySetting.objects.filter(
        company_id=job.company_id, category="notifications",
    ).values_list("data", flat=True).first() or {}
    if overrides.get(EVENT_SETTINGS[kind], True) is False:
        return 0

    # Every alert is backed by a unique, persisted audit event.
    event_id = str(activity.pk)
    already = set(Notification.objects.filter(
        company_id=job.company_id, notification_type=kind,
        data__eventId=event_id,
    ).values_list("user_id", flat=True))

    job_number = str(job.job_number or job.pk)[:60]
    registration = str(getattr(job.vehicle, "registration", "") or "").strip()[:30]
    vehicle_label = registration or "vehicle"
    base_route = f"/jobs/{job.pk}"

    if kind == "job_created":
        title = f"New vehicle arrived: {vehicle_label}"
        message = f"Job Card {job_number} was created for {vehicle_label}."
        route, priority = base_route, "Normal"
    elif kind == "job_assigned":
        title = f"Job assigned to you: {job_number}"
        message = f"You have been assigned Job Card {job_number} ({vehicle_label})."
        route, priority = f"{base_route}/work", "High"
    elif kind == "job_ready":
        title = f"Vehicle ready for delivery: {vehicle_label}"
        message = f"Job Card {job_number} is ready for delivery."
        route, priority = base_route, "High"
    else:
        title = f"Job Card updated: {job_number}"
        message = f"{vehicle_label}: status changed to {job.status}."
        route, priority = base_route, "Normal"

    recipients = User.objects.filter(
        company_id=job.company_id, is_active=True,
    ).select_related("role")
    if kind == "job_assigned":
        if not job.technician_id:
            return 0
        recipients = recipients.filter(pk=job.technician_id)

    notices = []
    for member in recipients:
        role = member.role
        if not role or not role.is_active or role.company_id != job.company_id:
            continue
        owner = member.pk == job.company.owner_id
        if not owner:
            if job.branch_id and member.branch_id and member.branch_id != job.branch_id:
                continue
            if not (role.allows("jobs.view") and role.allows("notifications.view")):
                continue
        if member.pk in already:
            continue
        notices.append(Notification(
            company_id=job.company_id,
            branch_id=job.branch_id,
            user_id=member.pk,
            title=title[:180],
            message=message,
            notification_type=kind,
            data={
                "route": route,
                "priority": priority,
                "jobId": str(job.pk),
                "jobNumber": job_number,
                "registration": registration,
                "eventId": event_id,
            },
        ))
    if notices:
        Notification.objects.bulk_create(notices, batch_size=200)
    return len(notices)
