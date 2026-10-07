from celery import shared_task
from django.utils import timezone

from .models import AttendanceRule, AttendanceSession
from .services import close_session, get_attendance_rule, scheduled_checkout_datetime


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

        if rule.attendance_mode not in {
            AttendanceRule.MODE_AUTO_CHECKOUT,
            AttendanceRule.MODE_HYBRID,
        }:
            continue

        cutoff=scheduled_checkout_datetime(record,employee,rule)
        if now < cutoff:
            continue

        close_session(
            session,
            closed_at=cutoff,
            auto_closed=True,
            note="Automatically closed at configured shift end.",
        )
        closed+=1

    return {"closed":closed}
