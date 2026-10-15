"""Opt-in, durable monthly credit billing email notices.

The wallet ledger and invoices are authoritative. This outbox is only an
at-least-once notification channel: SMTP acceptance cannot guarantee delivery
or exactly-once receipt if a worker dies after SMTP accepts the message.
"""
import logging
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from .models import BillingEmailNotice


LOG = logging.getLogger(__name__)
VALID_CATEGORIES = frozenset({"feature", "storage"})
VALID_STATES = frozenset({"paid", "insufficient"})
MAX_ATTEMPTS = 6


def record_billing_notice(company, category, period, state, credits, *, feature=None):
    """Deduplicate one event per active company billing administrator.

    Called in the same database transaction as invoice/ledger settlement;
    never schedules a Celery message before that transaction commits.
    """
    if not getattr(settings, "BILLING_EMAIL_ALERTS_ENABLED", False):
        return 0
    if category not in VALID_CATEGORIES or state not in VALID_STATES:
        raise ValueError("Unknown monthly billing email category/status.")
    if len(period) != 7 or period[4] != "-":
        raise ValueError("Billing period must be YYYY-MM.")
    amount = Decimal(str(credits)).quantize(Decimal("0.01"))
    if amount < 0:
        raise ValueError("Negative billing notice amount.")
    if amount == 0:
        return 0
    label = str(feature or category).replace("\n", " ").replace("\r", " ")[:90]
    subject = (
        "CubeGear: monthly credits deducted"
        if state == "paid" else
        "CubeGear: monthly credits payment required"
    )
    verb = "deducted from the company credit wallet" if state == "paid" else "still due (insufficient wallet credits)"
    message = (
        f"Your company {company.name} has {amount} wallet credits {verb} "
        f"for {label} usage during {period}.\n\n"
        "View your credit wallet and usage statements in CubeGear. "
        "If credits are insufficient, your administrator can buy credits and "
        "retry settlement. This is not an automatic bank or UPI withdrawal."
    )

    created = 0
    for user in User.objects.filter(company=company, is_active=True).exclude(email="").iterator():
        if not user_has_permission(user, "billing.edit"):
            continue
        # Hashing keeps the unique ref bounded even with external addresses.
        fingerprint = sha256(
            f"{company.pk}:{category}:{period}:{state}:{feature or ''}:{user.pk}".encode()
        ).hexdigest()[:32]
        _, was_created = BillingEmailNotice.objects.get_or_create(
            reference=f"billing-email:{fingerprint}",
            defaults={
                "company": company, "recipient": user.email,
                "subject": subject, "message": message,
            },
        )
        created += int(was_created)
    return created


def dispatch_pending_billing_notices(*, limit=25, now=None):
    """Worker-safe best-effort SMTP dispatch with bounded retries.

    Do not call from payment/web requests. Invoke from one Celery task/Beat.
    """
    if not getattr(settings, "BILLING_EMAIL_ALERTS_ENABLED", False):
        return {"disabled": 1}
    now = now or timezone.now()
    if not 1 <= int(limit) <= 100:
        raise ValueError("Outbox batch size must be between 1 and 100.")
    counts = {"sent": 0, "failed": 0}
    due_ids = list(
        BillingEmailNotice.objects.filter(
            status__in=["pending", "failed"],
            next_attempt_at__lte=now,
            attempts__lt=MAX_ATTEMPTS,
        ).order_by("next_attempt_at", "pk").values_list("pk", flat=True)[:int(limit)]
    )
    for pk in due_ids:
        with transaction.atomic():
            notice = BillingEmailNotice.objects.select_for_update().get(pk=pk)
            if (notice.status == "sent" or notice.attempts >= MAX_ATTEMPTS
                    or notice.next_attempt_at > now):
                continue
            try:
                result = send_mail(
                    notice.subject, notice.message, settings.DEFAULT_FROM_EMAIL,
                    [notice.recipient], fail_silently=False,
                )
                if result != 1:
                    raise RuntimeError("SMTP did not accept the notice.")
            except Exception as exc:
                notice.attempts += 1
                notice.status = "failed"
                notice.next_attempt_at = now + timedelta(
                    seconds=min(3600, 60 * 2 ** min(notice.attempts, 6)),
                )
                # Do not persist SMTP errors, which may include recipient data,
                # credentials or provider response details.
                notice.last_error = type(exc).__name__[:120]
                notice.save(update_fields=[
                    "attempts", "status", "next_attempt_at", "last_error",
                ])
                LOG.warning("Billing email delivery failed notice=%s", notice.pk)
                counts["failed"] += 1
            else:
                notice.attempts += 1
                notice.status = "sent"
                notice.sent_at = now
                notice.last_error = ""
                notice.save(update_fields=["attempts", "status", "sent_at", "last_error"])
                counts["sent"] += 1
    return counts
