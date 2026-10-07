from celery import shared_task
from django.utils import timezone

from .models import AttendanceRule, AttendanceSession
from .services import (
    auto_checkout_due_datetime,
    close_session,
    get_attendance_rule,
    scheduled_checkout_datetime,
)


@shared_task
def auto_close_attendance_sessions():
    now=timezone.now()
    closed=0

    sessions=(
        AttendanceSession.objects
        .filter(clock_out__isnull=True)
        .select_related("attendance","attendance__employee","attendance__branch","attendance__company")
    )

    for session in sessions.iterator():
        record=session.attendance
        employee=record.employee
        rule=get_attendance_rule(record.company,record.branch)

        auto_mode=rule.attendance_mode in {
            AttendanceRule.MODE_AUTO_CHECKOUT,
            AttendanceRule.MODE_HYBRID,
        }
        auto_missing=rule.missing_punch_policy=="auto_close"

        scheduled=scheduled_checkout_datetime(record,employee,rule)
        due=auto_checkout_due_datetime(record,employee,rule)
        if now < due:
            continue

        if auto_mode or auto_missing:
            close_session(
                session,
                closed_at=scheduled,
                auto_closed=True,
                note="Automatically closed at configured shift end.",
            )
            closed+=1
            continue

        if rule.missing_punch_policy in {"request_correction","mark_missing"}:
            if record.status != "Missing Clock Out":
                record.status="Missing Clock Out"
                record.save(update_fields=["status","updated_at"])

    return {"closed":closed}
