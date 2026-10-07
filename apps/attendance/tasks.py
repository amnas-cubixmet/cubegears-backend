from datetime import timedelta
from celery import shared_task
from django.utils import timezone

from apps.notifications.models import Notification
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
            if employee.user_id:
                Notification.objects.get_or_create(
                    company=record.company,
                    branch=record.branch,
                    user=employee.user,
                    notification_type="attendance_auto_checkout",
                    data={"recordId":str(record.id)},
                    defaults={
                        "title":"Attendance checked out automatically",
                        "message":f"Your attendance was checked out automatically at {scheduled.astimezone().strftime('%I:%M %p')}.",
                    },
                )
            continue

        if rule.missing_punch_policy in {"request_correction","mark_missing"}:
            if record.status != "Missing Clock Out":
                record.status="Missing Clock Out"
                record.save(update_fields=["status","updated_at"])

            reminder_due=scheduled + timedelta(minutes=rule.missing_punch_reminder_minutes or 0)
            if (
                rule.missing_punch_reminder_enabled
                and now >= reminder_due
                and employee.user_id
            ):
                Notification.objects.get_or_create(
                    company=record.company,
                    branch=record.branch,
                    user=employee.user,
                    notification_type="attendance_missing_punch",
                    data={"recordId":str(record.id)},
                    defaults={
                        "title":"Clock-out reminder",
                        "message":"Your shift has ended but your attendance is still open. Please check out or request a punch correction.",
                    },
                )

    return {"closed":closed}
