from datetime import datetime, timedelta

from django.db import IntegrityError
from django.db.models import Sum
from django.utils import timezone

from .models import AttendanceRecord, AttendanceRule, AttendanceSession


def get_attendance_rule(company, branch=None):
    qs = AttendanceRule.objects.filter(company=company, is_default=True)

    rule = qs.filter(branch__isnull=True).first()
    if rule:
        return rule

    # Normalize legacy branch-scoped defaults into the company-wide rule.
    legacy = qs.first()
    if legacy:
        legacy.branch = None
        legacy.save(update_fields=["branch", "updated_at"])
        return legacy

    return AttendanceRule.objects.create(
        company=company,
        branch=None,
        name="Default",
        weekend_days=["Sunday"],
        is_default=True,
    )


def get_shift_times(employee, rule):
    shift = getattr(employee, "shift", None)
    start = getattr(shift, "start_time", None) or rule.shift_start_time
    end = getattr(shift, "end_time", None) or rule.shift_end_time
    return start, end


def shift_datetime(day, value):
    return timezone.make_aware(
        datetime.combine(day, value),
        timezone.get_current_timezone(),
    )


def is_configured_weekly_off(day, rule):
    if rule.weekend_effective_from and day < rule.weekend_effective_from:
        return False

    if day.strftime("%A") in set(rule.weekend_days or []):
        return True

    if day.strftime("%A") != "Saturday" or not rule.alternate_saturday_enabled:
        return False

    occurrence=((day.day - 1) // 7) + 1
    pattern=rule.alternate_saturday_pattern or ""

    if pattern=="1st & 3rd Saturday":
        return occurrence in {1,3}
    if pattern=="2nd & 4th Saturday":
        return occurrence in {2,4}
    if pattern=="1st, 3rd & 5th Saturday":
        return occurrence in {1,3,5}
    if pattern=="All Saturdays":
        return True

    return False


def scheduled_checkout_datetime(record, employee, rule):
    _, end_time = get_shift_times(employee, rule)
    end_dt = shift_datetime(record.date, end_time)

    # Support overnight shifts such as 22:00 -> 06:00.
    start_time, _ = get_shift_times(employee, rule)
    if end_time <= start_time:
        end_dt += timedelta(days=1)

    return end_dt


def auto_checkout_due_datetime(record, employee, rule):
    return scheduled_checkout_datetime(record, employee, rule) + timedelta(
        minutes=rule.auto_checkout_grace_minutes or 0
    )


def recalculate_record(record, rule=None):
    rule = rule or get_attendance_rule(record.company, record.branch)
    sessions = list(record.sessions.order_by("session_number"))

    if not sessions:
        return record

    first = sessions[0]
    open_session = next((session for session in sessions if session.clock_out is None), None)
    completed_minutes = sum(int(session.worked_minutes or 0) for session in sessions if session.clock_out)

    record.clock_in = first.clock_in
    record.clock_out = None if open_session else sessions[-1].clock_out
    record.worked_minutes = completed_minutes

    if (
        is_configured_weekly_off(record.date, rule)
        and rule.weekend_attendance_policy == "allow_overtime"
    ):
        record.overtime_minutes = completed_minutes
    else:
        record.overtime_minutes = max(
            0,
            completed_minutes - int(rule.overtime_after_minutes or 0),
        )

    if record.clock_out:
        scheduled_end = scheduled_checkout_datetime(record, record.employee, rule)
        record.early_exit_minutes = max(
            0,
            int((scheduled_end - record.clock_out).total_seconds() // 60),
        )
    else:
        record.early_exit_minutes = 0

    if record.status not in {"On Leave", "Holiday", "Weekly Off"}:
        record.status = "Present"

    record.save(
        update_fields=[
            "clock_in",
            "clock_out",
            "worked_minutes",
            "early_exit_minutes",
            "overtime_minutes",
            "status",
            "updated_at",
        ]
    )
    return record


def close_session(session, closed_at=None, location=None, auto_closed=False, note=""):
    closed_at = closed_at or timezone.now()
    if closed_at < session.clock_in:
        closed_at = session.clock_in

    session.clock_out = closed_at
    session.worked_minutes = max(
        0,
        int((session.clock_out - session.clock_in).total_seconds() // 60),
    )
    session.auto_closed = auto_closed
    if location is not None:
        session.clock_out_location = location
    if note:
        session.note = note
    session.save(
        update_fields=[
            "clock_out",
            "worked_minutes",
            "auto_closed",
            "clock_out_location",
            "note",
            "updated_at",
        ]
    )

    recalculate_record(session.attendance)
    return session


def ensure_record_session(record):
    if not record or record.sessions.exists() or not record.clock_in:
        return

    AttendanceSession.objects.get_or_create(
        attendance=record,
        session_number=1,
        defaults={
            "company":record.company,
            "branch":record.branch,
            "clock_in":record.clock_in,
            "clock_out":record.clock_out,
            "worked_minutes":record.worked_minutes or 0,
            "auto_closed":False,
            "source":"legacy",
            "clock_in_location":record.location or {},
            "note":"Created from legacy attendance record",
        },
    )


def get_attendance_state(employee, day=None):
    day = day or timezone.localdate()
    rule = get_attendance_rule(employee.company, employee.branch)

    record = (
        AttendanceRecord.objects.filter(employee=employee, date=day)
        .prefetch_related("sessions")
        .first()
    )
    ensure_record_session(record)
    sessions = (
        list(AttendanceSession.objects.filter(attendance=record).order_by("session_number"))
        if record else []
    )
    open_session = next((session for session in sessions if session.clock_out is None), None)

    mode = rule.attendance_mode
    session_count = len(sessions)
    max_sessions = int(rule.max_sessions_per_day or 0)

    can_check_in = False
    can_check_out = False
    reason = ""

    if open_session:
        if mode == AttendanceRule.MODE_AUTO_CHECKOUT:
            reason = "Automatic checkout is enabled for this shift."
        else:
            can_check_out = True
    else:
        if mode in {
            AttendanceRule.MODE_SINGLE,
            AttendanceRule.MODE_AUTO_CHECKOUT,
            AttendanceRule.MODE_HYBRID,
        }:
            can_check_in = session_count == 0
            if session_count:
                reason = "Today's attendance is already completed."
        else:
            can_check_in = max_sessions == 0 or session_count < max_sessions
            if not can_check_in:
                reason = "Maximum attendance sessions reached for today."

    next_action = "check_out" if can_check_out else "check_in" if can_check_in else None
    auto_checkout_at = None
    if open_session and mode in {AttendanceRule.MODE_AUTO_CHECKOUT, AttendanceRule.MODE_HYBRID}:
        auto_checkout_at = scheduled_checkout_datetime(record, employee, rule)

    return {
        "rule": rule,
        "record": record,
        "sessions": sessions,
        "open_session": open_session,
        "can_check_in": can_check_in,
        "can_check_out": can_check_out,
        "next_action": next_action,
        "reason": reason,
        "auto_checkout_at": auto_checkout_at,
    }


def start_session(employee, location=None, source="web"):
    now = timezone.now()
    today = timezone.localdate()
    state = get_attendance_state(employee, today)
    rule = state["rule"]

    if not state["can_check_in"]:
        raise ValueError(state["reason"] or "Check-in is not available.")

    if is_configured_weekly_off(today, rule) and rule.weekend_attendance_policy == "weekly_off":
        raise ValueError(f"{today.strftime('%A')} is configured as a weekly off.")

    record, _ = AttendanceRecord.objects.get_or_create(
        company=employee.company,
        branch=employee.branch,
        employee=employee,
        date=today,
        defaults={"status": "Present"},
    )

    session_number = record.sessions.count() + 1
    try:
        session = AttendanceSession.objects.create(
            company=employee.company,
            branch=employee.branch,
            attendance=record,
            session_number=session_number,
            clock_in=now,
            source=source,
            clock_in_location=location or {},
        )
    except IntegrityError as exc:
        raise ValueError("Attendance was already updated. Refresh and try again.") from exc

    start_time, _ = get_shift_times(employee, rule)
    shift_start = shift_datetime(today, start_time)
    allowed_start = shift_start + timedelta(minutes=rule.grace_minutes or 0)
    if now > allowed_start and session_number == 1:
        record.late_minutes = max(0, int((now - shift_start).total_seconds() // 60))
        record.save(update_fields=["late_minutes", "updated_at"])

    recalculate_record(record, rule)
    return session, get_attendance_state(employee, today)


def finish_session(employee, location=None, source="web"):
    state = get_attendance_state(employee)
    rule = state["rule"]
    session = state["open_session"]

    if rule.attendance_mode == AttendanceRule.MODE_AUTO_CHECKOUT:
        raise ValueError("Manual checkout is disabled. Checkout will happen automatically.")

    if not session or not state["can_check_out"]:
        raise ValueError(state["reason"] or "There is no open attendance session.")

    session.source = source or session.source
    session.save(update_fields=["source", "updated_at"])
    close_session(session, location=location)
    return session, get_attendance_state(employee)
