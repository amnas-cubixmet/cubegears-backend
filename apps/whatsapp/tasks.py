"""Historical estimate validation and disabled compatibility hook.

The WhatsApp application registers no Celery tasks or automatic sends.
"""
import re
from apps.jobs.models import JobEstimate
from apps.jobs.workflow import workflow_state
from apps.customers.estimate_review import public_estimate

_ESTIMATE_EVENT_KEY = re.compile(
    r"^estimate:([0-9a-fA-F-]{36}):(shared|reminder:[1-9][0-9]*)$"
)


def _active_estimate(event, event_key, customer, job):
    """Check the saved quote at queue time AND immediately before provider send.

    Do not send messages about withdrawn, rejected, superseded or completed
    estimates. The event key refers only to a UUID, never the private link.
    """
    if event not in {"estimate_shared", "estimate_reminder"}:
        return None
    match = _ESTIMATE_EVENT_KEY.fullmatch(str(event_key))
    if not match or not job or not customer:
        return None
    if (match.group(2) == "shared") != (event == "estimate_shared"):
        return None
    if (job.company_id != customer.company_id or job.customer_id != customer.pk
            or job.vehicle.customer_id != customer.pk):
        return None
    quote = JobEstimate.objects.filter(
        pk=match.group(1), job=job, company=job.company,
        status="Awaiting Customer",
    ).first()
    if not quote or workflow_state(job)["current"] != "estimate":
        return None
    latest_id = job.estimates.order_by("-version", "-created_at").values_list(
        "pk", flat=True
    ).first()
    if latest_id != quote.pk or not public_estimate(quote)["reviewable"]:
        return None
    return quote


def queue_customer_notification(*args, **kwargs):
    """Legacy import compatibility: message delivery has been removed."""
    return None
