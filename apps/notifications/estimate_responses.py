"""Tenant-scoped, recipient-specific staff inbox alerts for customer quote decisions.

A customer decision must never mark the Job Card approved or send WhatsApp
messages on its own. The notification is committed with the existing decision
transaction so no phantom alert survives a rolled-back decision.
"""
from apps.accounts.models import User
from .models import Notification

ESTIMATE_ACCEPTED = "customer_estimate_accepted"
ESTIMATE_DECLINED = "customer_estimate_declined"
ESTIMATE_TYPES = frozenset({ESTIMATE_ACCEPTED, ESTIMATE_DECLINED})


def notify_customer_estimate_response(job, estimate, decision, activity):
    """Notify only active, company-scoped users who can view the job and inbox.

    Each user gets a private notification (not a shared user=NULL record), so
    reading one inbox never silently marks another staff member's alert read.
    The validated owner is included even if an old role lost explicit grants.
    Staff in a different branch cannot receive this Job Card's alert.
    """
    if decision not in {"accept", "decline"}:
        raise ValueError("Unknown customer estimate response.")
    company_id = job.company_id
    receipt_id = str(activity.pk)
    kind = ESTIMATE_ACCEPTED if decision == "accept" else ESTIMATE_DECLINED
    action = "accepted" if decision == "accept" else "declined"
    version = int(estimate.version)
    job_number = str(job.job_number or str(job.pk))[:60]
    route = f"/jobs/{job.pk}/estimate"
    details = {
        "route": route,
        "jobId": str(job.pk),
        "jobNumber": job_number,
        "estimateId": str(estimate.pk),
        "version": version,
        "decision": "Accepted" if decision == "accept" else "Declined",
        "receiptId": receipt_id,
        "source": "customer_portal_session",
    }

    # The portal decision holds a lock on the job row, so the same receipt
    # cannot be concurrently created for two decisions of the same version.
    already = set(Notification.objects.filter(
        company_id=company_id, notification_type=kind,
        data__receiptId=receipt_id,
    ).values_list("user_id", flat=True))
    notifications = []
    for member in User.objects.filter(company_id=company_id, is_active=True).select_related("role"):
        role = member.role
        if not role or not role.is_active or role.company_id != company_id:
            continue
        is_owner = member.pk == job.company.owner_id
        if not is_owner:
            if job.branch_id and member.branch_id and member.branch_id != job.branch_id:
                continue
            if not (role.allows("jobs.view") and role.allows("notifications.view")):
                continue
        if member.pk in already:
            continue
        notifications.append(Notification(
            company_id=company_id,
            branch_id=job.branch_id,
            user_id=member.pk,
            title=f"Customer {action} Estimate V{version}",
            message=(
                f"Job Card {job_number}: the customer {action} the saved estimate. "
                + ("Confirm the response before proceeding." if decision == "accept"
                   else "Review the decline and prepare a revised estimate.")
            ),
            notification_type=kind,
            data=details.copy(),
        ))

    if notifications:
        Notification.objects.bulk_create(notifications, batch_size=200)
    return len(notifications)
