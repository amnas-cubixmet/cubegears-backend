from decimal import Decimal, InvalidOperation
from datetime import datetime, time, timedelta
from django.db.models import Q
from django.utils import timezone
from rest_framework import response,status
from apps.accounts.permissions import RolePermission, user_has_permission
from apps.accounts.models import User
from apps.notifications.models import Notification
from apps.employees.models import Employee, Shift, Team
from apps.employees.services import resolve_employee_for_user
from rest_framework.views import APIView

from .models import AttendanceRecord,AttendanceSession,LeaveRequest,OvertimeRequest,Holiday,AttendanceRule,PunchCorrection,LeaveType
from .serializers import AttendanceRecordSerializer,AttendanceSessionSerializer,LeaveRequestSerializer,OvertimeRequestSerializer,HolidaySerializer,AttendanceRuleSerializer,PunchCorrectionSerializer,LeaveTypeSerializer
from .services import close_session, ensure_record_session, get_attendance_rule, is_configured_weekly_off, recalculate_record

UNPAID_LEAVE_NAME="Unpaid Leave"
UNPAID_LEAVE_CODE="UNPAID"


def _is_unpaid_leave_name(value):
    normalized=str(value or "").strip().casefold()
    return normalized in {UNPAID_LEAVE_NAME.casefold(),UNPAID_LEAVE_CODE.casefold()}


def _employee_for_user(user):
    return resolve_employee_for_user(user)


def _leave_days(start_date,end_date,half_day=False):
    if half_day:
        return 0.5
    return max(1,(end_date-start_date).days+1)


def _leave_period_bounds(leave_type,reference_date):
    if leave_type.allocation_method==LeaveType.ALLOCATION_MONTHLY:
        start=reference_date.replace(day=1)
        next_month=(start.replace(day=28)+timedelta(days=4)).replace(day=1)
        return start,next_month-timedelta(days=1)

    return reference_date.replace(month=1,day=1),reference_date.replace(month=12,day=31)


def _leave_period_allocation(leave_type):
    if leave_type.allocation_method==LeaveType.ALLOCATION_MONTHLY:
        return float(leave_type.monthly_allocation or 0)
    if leave_type.allocation_method==LeaveType.ALLOCATION_MANUAL:
        return float(leave_type.annual_allocation or 0)
    return float(leave_type.annual_allocation or 0)


def _leave_usage(employee,leave_type,reference_date):
    period_start,period_end=_leave_period_bounds(leave_type,reference_date)
    approved=0.0
    pending=0.0

    rows=LeaveRequest.objects.filter(
        employee=employee,
        leave_type=leave_type.name,
        status__in=["Approved","Pending"],
        start_date__lte=period_end,
        end_date__gte=period_start,
    )

    for row in rows:
        overlap_start=max(row.start_date,period_start)
        overlap_end=min(row.end_date,period_end)
        days=0.5 if row.half_day else _leave_days(overlap_start,overlap_end)
        if row.status=="Approved":
            approved+=days
        else:
            pending+=days

    return approved,pending


def _notify_attendance_managers(company,title,message,notification_type,data=None):
    users=User.objects.filter(company=company,is_active=True).select_related("role")
    notifications=[]
    for user in users:
        if user_has_permission(user,"attendance.manage"):
            notifications.append(Notification(
                company=company,
                branch=user.branch,
                user=user,
                title=title,
                message=message,
                notification_type=notification_type,
                data=data or {},
            ))
    if notifications:
        Notification.objects.bulk_create(notifications)


def _notify_employee(employee,title,message,notification_type,data=None):
    if not employee or not employee.user_id:
        return
    Notification.objects.create(
        company=employee.company,
        branch=employee.branch,
        user=employee.user,
        title=title,
        message=message,
        notification_type=notification_type,
        data=data or {},
    )


def _parse_attendance_datetime(day,value):
    if not value:
        return None
    if isinstance(value,datetime):
        parsed=value
    else:
        text=str(value).strip()
        parsed=None
        try:
            parsed=datetime.fromisoformat(text.replace("Z","+00:00"))
        except Exception:
            for fmt in ("%H:%M","%I:%M %p"):
                try:
                    parsed=datetime.combine(day,datetime.strptime(text,fmt).time())
                    break
                except Exception:
                    continue
        if parsed is None:
            return None
    if timezone.is_naive(parsed):
        parsed=timezone.make_aware(parsed,timezone.get_current_timezone())
    return parsed

class MyAttendanceLogsView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.self"
    def get(self,request):
        employee=_employee_for_user(request.user)
        if not employee: return response.Response([])
        qs=AttendanceRecord.objects.filter(employee=employee).prefetch_related("sessions")
        st=request.query_params.get("status")
        month=request.query_params.get("month")
        year=request.query_params.get("year")
        if st and st.upper()!="ALL":
            qs=qs.filter(status__iexact=st)
        if month:
            qs=qs.filter(date__month=month)
        if year:
            qs=qs.filter(date__year=year)
        return response.Response(AttendanceRecordSerializer(qs,many=True).data)

class MyAttendanceCalendarView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.self"
    def get(self,request):
        employee=_employee_for_user(request.user)
        if not employee: return response.Response([])
        month=int(request.query_params.get("month") or timezone.localdate().month)
        year=int(request.query_params.get("year") or timezone.localdate().year)
        records=AttendanceRecord.objects.filter(employee=employee,date__month=month,date__year=year)
        holidays=Holiday.objects.filter(company=request.user.company,date__month=month,date__year=year)
        leaves=LeaveRequest.objects.filter(
            employee=employee,
            status="Approved",
            start_date__lte=datetime(year,month,28).date()+timedelta(days=4),
            end_date__gte=datetime(year,month,1).date(),
        )
        rule=get_attendance_rule(request.user.company,None)
        first_day=datetime(year,month,1).date()
        next_month=(first_day.replace(day=28)+timedelta(days=4)).replace(day=1)
        last_day=next_month-timedelta(days=1)

        events=[{"id":str(x.id),"date":x.date,"type":"Attendance","status":x.status,"title":x.status} for x in records]
        recorded_dates={x.date for x in records}

        for holiday in holidays:
            if holiday.date not in recorded_dates:
                events.append({
                    "id":str(holiday.id),
                    "date":holiday.date,
                    "type":"Holiday",
                    "status":"Holiday",
                    "title":holiday.name,
                })

        for leave in leaves:
            cursor=max(leave.start_date,first_day)
            end=min(leave.end_date,last_day)
            while cursor<=end:
                if cursor not in recorded_dates:
                    events.append({
                        "id":f"leave-{leave.id}-{cursor}",
                        "date":cursor,
                        "type":"Leave",
                        "status":"On Leave",
                        "title":leave.leave_type,
                    })
                cursor+=timedelta(days=1)

        cursor=first_day
        while cursor<=last_day:
            if (
                cursor not in recorded_dates
                and is_configured_weekly_off(cursor,rule)
                and not any(str(event["date"])==cursor.isoformat() for event in events)
            ):
                events.append({
                    "id":f"weekly-off-{cursor}",
                    "date":cursor,
                    "type":"Weekly Off",
                    "status":"Weekly Off",
                    "title":"Weekly Off",
                })
            cursor+=timedelta(days=1)

        return response.Response(events)

class PunchCorrectionCreateView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.self"
    def post(self,request):
        employee=_employee_for_user(request.user)
        if not employee: return response.Response({"message":"No employee profile linked."},status=400)
        attendance_id=request.data.get("attendanceId")
        attendance=AttendanceRecord.objects.filter(pk=attendance_id,employee=employee).first()
        if not attendance: return response.Response({"message":"Attendance record not found."},status=404)
        proposed=request.data.get("proposedClockOut")
        proposed_dt=None
        if proposed:
            for fmt in ("%H:%M","%I:%M %p"):
                try:
                    t=datetime.strptime(proposed,fmt).time()
                    proposed_dt=timezone.make_aware(datetime.combine(attendance.date,t))
                    break
                except Exception:
                    continue
            if proposed_dt is None:
                try:
                    proposed_dt=datetime.fromisoformat(proposed)
                    if timezone.is_naive(proposed_dt):
                        proposed_dt=timezone.make_aware(proposed_dt)
                except Exception:
                    pass
        rule=get_attendance_rule(request.user.company,request.user.branch)
        obj=PunchCorrection.objects.create(
            company=request.user.company,branch=request.user.branch,attendance=attendance,employee=employee,
            original_clock_out=attendance.clock_out,proposed_clock_out=proposed_dt,
            reason=request.data.get("reason") or "",
            status="Approved" if not rule.correction_approval else "Pending",
            reviewed_by=request.user if not rule.correction_approval else None,
            reviewed_at=timezone.now() if not rule.correction_approval else None,
        )
        if not rule.correction_approval and proposed_dt:
            open_session=attendance.sessions.filter(clock_out__isnull=True).order_by("-session_number").first()
            if open_session:
                close_session(
                    open_session,
                    closed_at=proposed_dt,
                    note="Punch correction applied without manager approval.",
                )
            else:
                attendance.clock_out=proposed_dt
                if attendance.clock_in:
                    attendance.worked_minutes=max(0,int((attendance.clock_out-attendance.clock_in).total_seconds()//60))
                attendance.status="Present"
                attendance.save(update_fields=["clock_out","worked_minutes","status","updated_at"])
        return response.Response(PunchCorrectionSerializer(obj).data,status=201)

class LeaveBalancesView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.self"

    def get(self,request):
        employee=_employee_for_user(request.user)
        if not employee:
            return response.Response([])

        today=timezone.localdate()
        types=LeaveType.objects.filter(
            company=request.user.company,
            status="Active",
        ).order_by("name")

        result=[]
        for leave_type in types:
            # Unpaid Leave is a built-in system option. Configured company leave
            # types are always treated as paid leave.
            if _is_unpaid_leave_name(leave_type.name) or str(leave_type.code or "").upper()==UNPAID_LEAVE_CODE:
                continue

            allocated=_leave_period_allocation(leave_type)

            # Paid leave types without an allocation are not available to employees.
            if allocated<=0:
                continue

            approved,pending=_leave_usage(employee,leave_type,today)
            remaining=max(0,allocated-approved)
            available=max(0,remaining-pending)

            result.append({
                "id":str(leave_type.id),
                "code":leave_type.code,
                "type":leave_type.name,
                "paidType":"Paid",
                "isPaid":True,
                "isUnpaid":False,
                "unlimited":False,
                "allocationMethod":leave_type.allocation_method,
                "allocationPeriod":"month" if leave_type.allocation_method==LeaveType.ALLOCATION_MONTHLY else "year",
                "allocated":allocated,
                "used":approved,
                "pending":pending,
                "remaining":remaining,
                "available":available,
                "halfDayAllowed":leave_type.half_day,
            })

        unpaid_rows=LeaveRequest.objects.filter(
            employee=employee,
            leave_type=UNPAID_LEAVE_NAME,
            status__in=["Approved","Pending"],
            start_date__year=today.year,
        )
        unpaid_used=0.0
        unpaid_pending=0.0
        for row in unpaid_rows:
            days=_leave_days(row.start_date,row.end_date,row.half_day)
            if row.status=="Approved":
                unpaid_used+=days
            else:
                unpaid_pending+=days

        result.append({
            "id":UNPAID_LEAVE_CODE,
            "code":UNPAID_LEAVE_CODE,
            "type":UNPAID_LEAVE_NAME,
            "paidType":"Unpaid",
            "isPaid":False,
            "isUnpaid":True,
            "unlimited":True,
            "allocationMethod":"system",
            "allocationPeriod":"none",
            "allocated":None,
            "used":unpaid_used,
            "pending":unpaid_pending,
            "remaining":None,
            "available":None,
            "halfDayAllowed":True,
        })

        return response.Response(result)


class MyLeaveRequestsView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.self"

    def get(self,request):
        employee=_employee_for_user(request.user)
        if not employee:
            return response.Response([])
        return response.Response(
            LeaveRequestSerializer(
                LeaveRequest.objects.filter(employee=employee),
                many=True,
            ).data
        )

    def post(self,request):
        employee=_employee_for_user(request.user)
        if not employee:
            return response.Response({"message":"No employee profile linked."},status=400)

        leave_name=(request.data.get("leaveType") or request.data.get("leave_type") or "").strip()
        is_unpaid=_is_unpaid_leave_name(leave_name)
        leave_type=None

        if is_unpaid:
            leave_name=UNPAID_LEAVE_NAME
        else:
            leave_type=LeaveType.objects.filter(
                company=request.user.company,
                name=leave_name,
                status="Active",
            ).exclude(code__iexact=UNPAID_LEAVE_CODE).first()
            if not leave_type:
                return response.Response(
                    {"message":"Select an active paid leave type or Unpaid Leave."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        start_value=request.data.get("startDate") or request.data.get("start_date")
        end_value=request.data.get("endDate") or request.data.get("end_date") or start_value
        try:
            start_date=datetime.strptime(str(start_value),"%Y-%m-%d").date()
            end_date=datetime.strptime(str(end_value),"%Y-%m-%d").date()
        except (TypeError,ValueError):
            return response.Response({"message":"A valid leave date is required."},status=400)

        if end_date<start_date:
            return response.Response(
                {"message":"Leave end date cannot be before start date."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        half_day=bool(request.data.get("halfDay") or request.data.get("half_day") or False)
        if not is_unpaid and half_day and not leave_type.half_day:
            return response.Response(
                {"message":f"{leave_type.name} does not allow half-day requests."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        requested=_leave_days(start_date,end_date,half_day)

        if not is_unpaid:
            if (
                leave_type.allocation_method==LeaveType.ALLOCATION_MONTHLY
                and (start_date.year,start_date.month)!=(end_date.year,end_date.month)
            ):
                return response.Response(
                    {"message":"Monthly leave requests must stay within the same calendar month."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            allocation=_leave_period_allocation(leave_type)
            if allocation<=0:
                return response.Response(
                    {"message":f"{leave_type.name} currently has no paid leave allocation."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            approved,pending=_leave_usage(employee,leave_type,start_date)
            available=max(0,allocation-approved-pending)

            if requested>available:
                return response.Response(
                    {
                        "message":f"Only {available:g} day(s) of {leave_type.name} are available for this period. Use Unpaid Leave if paid balance is not available.",
                        "available":available,
                        "requested":requested,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        overlaps=LeaveRequest.objects.filter(
            employee=employee,
            status__in=["Pending","Approved"],
            start_date__lte=end_date,
            end_date__gte=start_date,
        ).exists()
        if overlaps:
            return response.Response(
                {"message":"A pending or approved leave request already exists for this date."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        obj=LeaveRequest.objects.create(
            company=request.user.company,
            branch=request.user.branch,
            employee=employee,
            leave_type=UNPAID_LEAVE_NAME if is_unpaid else leave_type.name,
            start_date=start_date,
            end_date=end_date,
            half_day=half_day,
            reason=request.data.get("reason") or "",
            attachment=request.data.get("attachment") or "",
            manager_note="",
        )

        rule=get_attendance_rule(request.user.company,None)
        if rule.leave_request_notifications:
            _notify_attendance_managers(
                request.user.company,
                "New leave request",
                f"{employee.name} submitted {obj.leave_type} from {obj.start_date} to {obj.end_date}.",
                "attendance_leave_request",
                {"requestId":str(obj.id),"staffId":str(employee.id),"route":"/attendance-manager/leave-requests"},
            )

        return response.Response(LeaveRequestSerializer(obj).data,status=201)


class CancelLeaveRequestView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.self"
    def post(self,request,pk):
        employee=_employee_for_user(request.user)
        obj=LeaveRequest.objects.filter(pk=pk,employee=employee,status="Pending").first()
        if not obj: return response.Response({"message":"Pending leave request not found."},status=404)
        obj.status="Cancelled"; obj.save(update_fields=["status","updated_at"])
        return response.Response(LeaveRequestSerializer(obj).data)

class ManagerApprovalsView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"
    def get(self,request):
        company=request.user.company
        rows=[]
        for x in PunchCorrection.objects.filter(company=company,status="Pending").select_related("employee"):
            rows.append({"id":str(x.id),"type":"Punch Correction","staffId":str(x.employee_id),"staffName":x.employee.name,"status":x.status,"reason":x.reason,"date":x.attendance.date})
        for x in LeaveRequest.objects.filter(company=company,status="Pending").select_related("employee"):
            total_days=0.5 if x.half_day else max(1,(x.end_date-x.start_date).days+1)
            rows.append({
                "id":str(x.id),
                "type":"Leave",
                "staffId":x.employee.employee_code,
                "employeeId":str(x.employee_id),
                "staffName":x.employee.name,
                "status":x.status,
                "reason":x.reason,
                "date":x.start_date,
                "startDate":x.start_date,
                "endDate":x.end_date,
                "totalDays":total_days,
                "leaveType":x.leave_type,
                "halfDay":x.half_day,
                "managerNote":x.manager_note,
            })
        for x in OvertimeRequest.objects.filter(company=company,status="Pending").select_related("employee"):
            rows.append({
                "id":str(x.id),
                "type":"Overtime",
                "staffId":x.employee.employee_code,
                "employeeId":str(x.employee_id),
                "staffName":x.employee.name,
                "status":x.status,
                "reason":x.reason,
                "date":x.date,
                "affectedDate":x.date,
                "minutes":x.minutes,
                "overtimeHours":round(x.minutes/60,2),
                "rate":float(x.rate or 0),
                "amount":float(x.amount or 0),
            })
        return response.Response(rows)

class ManagerApprovalDetailView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"
    def post(self,request,pk):
        decision=request.data.get("decision")
        next_status="Approved" if decision=="approve" else "Rejected"
        note=request.data.get("note") or ""
        rule=get_attendance_rule(request.user.company,request.user.branch)
        for Model in (PunchCorrection,LeaveRequest,OvertimeRequest):
            obj=Model.objects.filter(pk=pk,company=request.user.company).select_related("employee__user").first()
            if not obj: continue
            if (
                not rule.allow_self_approval
                and getattr(obj.employee,"user_id",None)==request.user.id
            ):
                return response.Response(
                    {"message":"Self-approval is disabled by attendance rules."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            obj.status=next_status
            if isinstance(obj,PunchCorrection):
                obj.manager_note=note; obj.reviewed_by=request.user; obj.reviewed_at=timezone.now()
                if next_status=="Approved" and obj.proposed_clock_out:
                    att=obj.attendance
                    open_session=att.sessions.filter(clock_out__isnull=True).order_by("-session_number").first()
                    if open_session:
                        close_session(
                            open_session,
                            closed_at=obj.proposed_clock_out,
                            note="Manager-approved punch correction.",
                        )
                    else:
                        att.clock_out=obj.proposed_clock_out
                        if att.clock_in:
                            att.worked_minutes=max(0,int((att.clock_out-att.clock_in).total_seconds()//60))
                        att.status="Present"
                        att.save()
            elif isinstance(obj,LeaveRequest):
                obj.reviewed_by=request.user
                obj.reviewed_at=timezone.now()
                obj.manager_note=note
            else:
                if next_status=="Approved":
                    raw_rate=request.data.get("rate")
                    if raw_rate in (None,""):
                        from apps.payroll.models import SalaryStructure
                        structure=SalaryStructure.objects.filter(employee=obj.employee).first()
                        raw_rate=(structure.overtime_rate if structure else obj.rate)

                    try:
                        rate=Decimal(str(raw_rate or 0))
                    except (InvalidOperation,TypeError,ValueError):
                        return response.Response(
                            {"message":"Enter a valid overtime rate."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                    if rate<=0:
                        return response.Response(
                            {"message":"Overtime rate is required before approval."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                    amount=((Decimal(obj.minutes)/Decimal("60"))*rate).quantize(Decimal("0.01"))
                    obj.rate=rate
                    obj.amount=amount
                    obj.approved_by=request.user
                    obj.approved_at=timezone.now()
                    obj.rejection_reason=""
                else:
                    obj.approved_by=None
                    obj.approved_at=None
                    obj.rejection_reason=note

                history=list(obj.audit_history or [])
                history.insert(0,{
                    "action":f"{next_status} Overtime",
                    "actor":request.user.name,
                    "timestamp":timezone.now().isoformat(),
                    "note":note,
                    "rate":float(obj.rate or 0),
                    "amount":float(obj.amount or 0),
                })
                obj.audit_history=history
            obj.save()
            request_type=(
                "Punch correction" if isinstance(obj,PunchCorrection)
                else "Leave request" if isinstance(obj,LeaveRequest)
                else "Overtime request"
            )
            route=(
                "/my-attendance/leave" if isinstance(obj,LeaveRequest)
                else "/my-attendance/overtime" if isinstance(obj,OvertimeRequest)
                else "/my-attendance/history"
            )
            _notify_employee(
                obj.employee,
                f"{request_type} {obj.status.lower()}",
                (
                    f"Your overtime request has been approved for ₹{obj.amount}."
                    if isinstance(obj,OvertimeRequest) and obj.status=="Approved"
                    else f"Your {request_type.lower()} has been {obj.status.lower()}."
                ),
                "attendance_request_decision",
                {
                    "requestId":str(obj.id),
                    "status":obj.status,
                    "rate":float(obj.rate or 0) if isinstance(obj,OvertimeRequest) else None,
                    "amount":float(obj.amount or 0) if isinstance(obj,OvertimeRequest) else None,
                    "route":route,
                },
            )
            return response.Response({"id":str(obj.id),"status":obj.status})
        return response.Response({"message":"Approval not found."},status=404)

class ManagerTeamView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"

    def get(self,request):
        company=request.user.company
        date_value=request.query_params.get("date")
        try:
            day=datetime.strptime(date_value,"%Y-%m-%d").date() if date_value else timezone.localdate()
        except ValueError:
            return response.Response({"message":"Invalid date."},status=400)

        employees=Employee.objects.filter(company=company,status="Active").select_related("branch","shift","team","user")
        branch=request.query_params.get("branch")
        role=request.query_params.get("role")
        search=request.query_params.get("search") or ""

        if branch and branch!="All":
            employees=employees.filter(branch__name=branch)
        if role and role!="All":
            employees=employees.filter(designation=role)
        if search:
            employees=employees.filter(
                Q(name__icontains=search)|
                Q(employee_code__icontains=search)|
                Q(phone__icontains=search)|
                Q(designation__icontains=search)
            )

        records={
            record.employee_id:record
            for record in AttendanceRecord.objects.filter(company=company,date=day)
            .select_related("employee","branch")
            .prefetch_related("sessions")
        }
        leaves={
            row.employee_id:row
            for row in LeaveRequest.objects.filter(
                company=company,status="Approved",start_date__lte=day,end_date__gte=day
            )
        }
        holiday=Holiday.objects.filter(company=company,date=day).first()
        rule=get_attendance_rule(company,None)
        rows=[]

        for employee in employees:
            record=records.get(employee.id)
            sessions=list(record.sessions.all()) if record else []
            leave=leaves.get(employee.id)

            if record:
                row_status=record.status
            elif holiday:
                row_status="Holiday"
            elif leave:
                row_status="On Leave"
            elif is_configured_weekly_off(day,rule):
                row_status="Weekly Off"
            elif day <= timezone.localdate():
                row_status="Absent"
            else:
                row_status="Not Scheduled"

            first_session=sessions[0] if sessions else None
            last_session=sessions[-1] if sessions else None
            location_enabled=bool(rule.location_tracking_enabled or rule.location_required)
            live_worked_minutes=record.worked_minutes if record else 0
            open_session=next((session for session in sessions if session.clock_out is None),None)
            if open_session:
                live_worked_minutes += max(
                    0,
                    int((timezone.now()-open_session.clock_in).total_seconds()//60),
                )

            serialized_sessions=[]
            for session in sessions:
                data=AttendanceSessionSerializer(session).data
                if not location_enabled:
                    data["clock_in_location"]={}
                    data["clock_out_location"]={}
                serialized_sessions.append(data)

            rows.append({
                "id":str(record.id) if record else f"staff-{employee.id}-{day.isoformat()}",
                "attendanceId":str(record.id) if record else None,
                "employeeId":str(employee.id),
                "staffId":employee.employee_code,
                "employeeCode":employee.employee_code,
                "name":employee.name,
                "staffName":employee.name,
                "phone":employee.phone,
                "designation":employee.designation,
                "branch":employee.branch.name if employee.branch else "",
                "team":employee.team.name if employee.team else "",
                "shift":employee.shift.name if employee.shift else employee.shift_label,
                "date":day,
                "clockIn":first_session.clock_in if first_session else None,
                "clockOut":last_session.clock_out if last_session else None,
                "workedMinutes":record.worked_minutes if record else 0,
                "liveWorkedMinutes":live_worked_minutes,
                "worked":f"{live_worked_minutes//60}h {live_worked_minutes%60}m",
                "lateMinutes":record.late_minutes if record else 0,
                "earlyExitMinutes":record.early_exit_minutes if record else 0,
                "overtimeMinutes":record.overtime_minutes if record else 0,
                "overtimeHours":round((record.overtime_minutes if record else 0)/60,2),
                "status":row_status,
                "notes":record.notes if record else "",
                "sessionCount":len(sessions),
                "sessions":serialized_sessions,
                "locationEnabled":location_enabled,
            })

        st=request.query_params.get("status")
        if st and st!="All":
            rows=[row for row in rows if row["status"]==st]
        return response.Response(rows)

    def post(self,request):
        employee=Employee.objects.filter(
            pk=request.data.get("employeeId"),
            company=request.user.company,
        ).first()
        if not employee:
            return response.Response({"message":"Employee not found."},status=404)

        date_value=request.data.get("date") or timezone.localdate().isoformat()
        try:
            day=datetime.strptime(str(date_value),"%Y-%m-%d").date()
        except ValueError:
            return response.Response({"message":"Invalid date."},status=400)

        record,_=AttendanceRecord.objects.get_or_create(
            company=request.user.company,
            branch=employee.branch,
            employee=employee,
            date=day,
            defaults={"status":request.data.get("status") or "Present"},
        )
        record.status=request.data.get("status") or record.status
        record.notes=request.data.get("auditReason") or request.data.get("notes") or record.notes
        record.save(update_fields=["status","notes","updated_at"])

        clock_in=_parse_attendance_datetime(day,request.data.get("clockIn"))
        clock_out=_parse_attendance_datetime(day,request.data.get("clockOut"))
        if clock_in:
            session=record.sessions.order_by("session_number").first()
            if not session:
                session=AttendanceSession.objects.create(
                    company=request.user.company,
                    branch=employee.branch,
                    attendance=record,
                    session_number=1,
                    clock_in=clock_in,
                    source="manager",
                    note="Attendance created by manager.",
                )
            else:
                session.clock_in=clock_in
                session.save(update_fields=["clock_in","updated_at"])
            if clock_out:
                close_session(
                    session,
                    closed_at=clock_out,
                    note="Attendance created by manager.",
                )
            else:
                recalculate_record(record)

        return response.Response(AttendanceRecordSerializer(record).data,status=201)

class ManagerTeamDetailView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"

    def put(self,request,pk):
        obj=AttendanceRecord.objects.filter(pk=pk,company=request.user.company).prefetch_related("sessions").first()
        if not obj:
            return response.Response({"message":"Attendance record not found."},status=404)

        for incoming,attr in [
            ("status","status"),
            ("workedMinutes","worked_minutes"),
            ("lateMinutes","late_minutes"),
            ("earlyExitMinutes","early_exit_minutes"),
            ("overtimeMinutes","overtime_minutes"),
        ]:
            if incoming in request.data:
                setattr(obj,attr,request.data[incoming])

        ensure_record_session(obj)
        sessions=list(obj.sessions.order_by("session_number"))
        clock_in=_parse_attendance_datetime(obj.date,request.data.get("clockIn")) if "clockIn" in request.data else None
        clock_out=_parse_attendance_datetime(obj.date,request.data.get("clockOut")) if "clockOut" in request.data else None

        if sessions and "clockIn" in request.data and clock_in:
            first=sessions[0]
            first.clock_in=clock_in
            first.save(update_fields=["clock_in","updated_at"])

        if sessions and "clockOut" in request.data:
            last=sessions[-1]
            if clock_out:
                close_session(last,closed_at=clock_out,note="Manager corrected attendance time.")
            else:
                last.clock_out=None
                last.worked_minutes=0
                last.save(update_fields=["clock_out","worked_minutes","updated_at"])

        obj.notes=request.data.get("auditReason") or request.data.get("notes") or obj.notes
        obj.save(update_fields=["status","worked_minutes","late_minutes","early_exit_minutes","overtime_minutes","notes","updated_at"])
        if sessions:
            recalculate_record(obj)
        return response.Response(AttendanceRecordSerializer(obj).data)

class ManagerMasterView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"
    def get(self,request):
        qs=AttendanceRecord.objects.filter(company=request.user.company).select_related("employee","branch").prefetch_related("sessions")
        month=request.query_params.get("month"); year=request.query_params.get("year")
        if month: qs=qs.filter(date__month=month)
        if year: qs=qs.filter(date__year=year)
        return response.Response(AttendanceRecordSerializer(qs[:1000],many=True).data)

class LeaveTypesView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"

    def get(self,request):
        qs=(
            LeaveType.objects.filter(company=request.user.company)
            .exclude(code__iexact=UNPAID_LEAVE_CODE)
            .exclude(name__iexact=UNPAID_LEAVE_NAME)
            .order_by("name")
        )
        data=LeaveTypeSerializer(qs,many=True).data
        for item in data:
            item["type"]="Paid"
        return response.Response(data)

    def post(self,request):
        data=request.data.copy()
        requested_name=str(data.get("name") or "").strip()
        requested_code=str(data.get("code") or "").strip().upper()
        if _is_unpaid_leave_name(requested_name) or requested_code==UNPAID_LEAVE_CODE:
            return response.Response(
                {"message":"Unpaid Leave is a built-in system leave type and does not need configuration."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data["type"]="Paid"

        method=data.get("allocationMethod") or data.get("allocation_method")
        if method=="Fixed Annual Allocation":
            data["allocationMethod"]=LeaveType.ALLOCATION_ANNUAL
        elif method=="Monthly Accrual":
            data["allocationMethod"]=LeaveType.ALLOCATION_MONTHLY
        elif method=="Manual Adjustment Only":
            data["allocationMethod"]=LeaveType.ALLOCATION_MANUAL

        ser=LeaveTypeSerializer(data=data)
        ser.is_valid(raise_exception=True)
        obj=ser.save(company=request.user.company,branch=request.user.branch)
        return response.Response(LeaveTypeSerializer(obj).data,status=201)


class LeaveTypeDetailView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"

    def _get(self,request,pk):
        return LeaveType.objects.filter(pk=pk,company=request.user.company).first()

    def patch(self,request,pk):
        obj=self._get(request,pk)
        if not obj:
            return response.Response({"message":"Leave type not found."},status=404)

        data=request.data.copy()
        requested_name=str(data.get("name") or obj.name or "").strip()
        requested_code=str(data.get("code") or obj.code or "").strip().upper()
        if _is_unpaid_leave_name(requested_name) or requested_code==UNPAID_LEAVE_CODE:
            return response.Response(
                {"message":"Unpaid Leave is a built-in system leave type and cannot be configured here."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data["type"]="Paid"
        method=data.get("allocationMethod") or data.get("allocation_method")
        if method=="Fixed Annual Allocation":
            data["allocationMethod"]=LeaveType.ALLOCATION_ANNUAL
        elif method=="Monthly Accrual":
            data["allocationMethod"]=LeaveType.ALLOCATION_MONTHLY
        elif method=="Manual Adjustment Only":
            data["allocationMethod"]=LeaveType.ALLOCATION_MANUAL

        ser=LeaveTypeSerializer(obj,data=data,partial=True)
        ser.is_valid(raise_exception=True)
        obj=ser.save()
        return response.Response(LeaveTypeSerializer(obj).data)

    def put(self,request,pk):
        return self.patch(request,pk)

    def delete(self,request,pk):
        obj=self._get(request,pk)
        if not obj:
            return response.Response({"message":"Leave type not found."},status=404)
        obj.status="Inactive"
        obj.save(update_fields=["status","updated_at"])
        return response.Response(LeaveTypeSerializer(obj).data)

class ManagerHolidaysView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"

    def get(self,request):
        qs=Holiday.objects.filter(company=request.user.company).order_by("date")
        year=request.query_params.get("year")
        if year:
            qs=qs.filter(date__year=year)
        return response.Response(HolidaySerializer(qs,many=True).data)

    def post(self,request):
        serializer=HolidaySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        obj=serializer.save(company=request.user.company,branch=request.user.branch)
        return response.Response(HolidaySerializer(obj).data,status=201)


class ManagerHolidayDetailView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"

    def delete(self,request,pk):
        obj=Holiday.objects.filter(pk=pk,company=request.user.company).first()
        if not obj:
            return response.Response({"message":"Holiday not found."},status=404)
        obj.delete()
        return response.Response(status=204)


class ManagerCalendarView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"

    def get(self,request):
        today=timezone.localdate()
        month=int(request.query_params.get("month") or today.month)
        year=int(request.query_params.get("year") or today.year)
        if month<1 or month>12:
            return response.Response({"message":"Invalid month."},status=400)

        company=request.user.company
        first_day=datetime(year,month,1).date()
        next_month=(first_day.replace(day=28)+timedelta(days=4)).replace(day=1)
        last_day=next_month-timedelta(days=1)
        employees=list(
            Employee.objects.filter(company=company,status="Active")
            .select_related("branch","shift","team")
            .order_by("name")
        )
        records=AttendanceRecord.objects.filter(
            company=company,date__gte=first_day,date__lte=last_day
        ).select_related("employee")
        record_map={(row.employee_id,row.date):row for row in records}
        leaves=LeaveRequest.objects.filter(
            company=company,status="Approved",start_date__lte=last_day,end_date__gte=first_day
        )
        leave_rows=list(leaves)
        holidays=list(Holiday.objects.filter(company=company,date__gte=first_day,date__lte=last_day))
        holiday_map={row.date:row for row in holidays}
        rule=get_attendance_rule(company,None)

        result=[]
        for employee in employees:
            day_map={}
            for day_num in range(1,last_day.day+1):
                day=datetime(year,month,day_num).date()
                record=record_map.get((employee.id,day))
                leave=next((x for x in leave_rows if x.employee_id==employee.id and x.start_date<=day<=x.end_date),None)
                holiday=holiday_map.get(day)

                if record:
                    status_value=record.status
                elif holiday:
                    status_value="Holiday"
                elif leave:
                    status_value="On Leave"
                elif is_configured_weekly_off(day,rule):
                    status_value="Weekly Off"
                elif day <= today:
                    status_value="Absent"
                else:
                    status_value=""

                code={
                    "Present":"P",
                    "Absent":"A",
                    "On Leave":"L",
                    "Half Day":"H",
                    "Weekly Off":"WO",
                    "Holiday":"HD",
                    "Missing Clock Out":"M",
                }.get(status_value,"")
                day_map[str(day_num)]={
                    "status":status_value,
                    "code":code,
                    "recordId":str(record.id) if record else None,
                }

            result.append({
                "employeeId":str(employee.id),
                "staffId":employee.employee_code,
                "name":employee.name,
                "designation":employee.designation,
                "branch":employee.branch.name if employee.branch else "",
                "days":day_map,
            })

        return response.Response({
            "month":month,
            "year":year,
            "days":last_day.day,
            "employees":result,
            "holidays":HolidaySerializer(holidays,many=True).data,
        })


class MyOvertimeRequestsView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.self"

    def get(self,request):
        employee=_employee_for_user(request.user)
        if not employee:
            return response.Response([])
        rows=OvertimeRequest.objects.filter(employee=employee).select_related("approved_by")
        return response.Response(OvertimeRequestSerializer(rows,many=True).data)

    def post(self,request):
        employee=_employee_for_user(request.user)
        if not employee:
            return response.Response({"message":"No employee profile linked."},status=400)

        date_value=request.data.get("date") or timezone.localdate().isoformat()
        try:
            request_date=datetime.strptime(str(date_value),"%Y-%m-%d").date()
        except ValueError:
            return response.Response({"message":"Invalid overtime date."},status=400)

        minutes=int(request.data.get("minutes") or round(float(request.data.get("overtimeHours") or 0)*60))
        if minutes<=0:
            return response.Response({"message":"Overtime minutes must be greater than zero."},status=400)

        obj=OvertimeRequest.objects.create(
            company=request.user.company,
            branch=request.user.branch,
            employee=employee,
            date=request_date,
            minutes=minutes,
            reason=request.data.get("reason") or "",
            payroll_month=request.data.get("payrollMonth") or request_date.strftime("%Y-%m"),
            status="Pending",
            audit_history=[{
                "action":"Overtime Submitted",
                "actor":request.user.name,
                "timestamp":timezone.now().isoformat(),
            }],
        )
        rule=get_attendance_rule(request.user.company,None)
        if rule.overtime_request_notifications:
            _notify_attendance_managers(
                request.user.company,
                "New overtime request",
                f"{employee.name} requested {round(minutes/60,2)} hours overtime for {request_date}.",
                "attendance_overtime_request",
                {"requestId":str(obj.id),"staffId":str(employee.id),"route":"/attendance-manager/overtime"},
            )
        return response.Response(OvertimeRequestSerializer(obj).data,status=201)


class CancelOvertimeRequestView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.self"

    def post(self,request,pk):
        employee=_employee_for_user(request.user)
        obj=OvertimeRequest.objects.filter(pk=pk,employee=employee,status="Pending").first()
        if not obj:
            return response.Response({"message":"Pending overtime request not found."},status=404)
        obj.status="Cancelled"
        obj.save(update_fields=["status","updated_at"])
        return response.Response(OvertimeRequestSerializer(obj).data)


class ManagerShiftsView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"

    def get(self,request):
        company=request.user.company
        shifts=Shift.objects.filter(company=company).order_by("name")
        teams=Team.objects.filter(company=company,is_active=True).order_by("name")
        employees=Employee.objects.filter(company=company,status="Active").select_related("team","shift").order_by("name")
        return response.Response({
            "shifts":[{
                "id":str(row.id),
                "name":row.name,
                "startTime":row.start_time,
                "endTime":row.end_time,
                "breakMinutes":row.break_minutes,
                "weeklyOff":row.weekly_off,
                "isActive":row.is_active,
                "staffCount":row.employees.filter(status="Active").count(),
            } for row in shifts],
            "teams":[{
                "id":str(row.id),
                "name":row.name,
                "staffCount":row.employees.filter(status="Active").count(),
            } for row in teams],
            "employees":[{
                "id":str(row.id),
                "staffId":row.employee_code,
                "name":row.name,
                "teamId":str(row.team_id) if row.team_id else None,
                "team":row.team.name if row.team else "",
                "shiftId":str(row.shift_id) if row.shift_id else None,
                "shift":row.shift.name if row.shift else row.shift_label,
            } for row in employees],
        })

    def post(self,request):
        company=request.user.company
        action=request.data.get("action") or "assign"

        if action=="save_shift":
            shift_id=request.data.get("shiftId")
            shift=Shift.objects.filter(pk=shift_id,company=company).first() if shift_id else None
            values={
                "name":request.data.get("name") or "General Shift",
                "start_time":request.data.get("startTime") or "09:00",
                "end_time":request.data.get("endTime") or "18:00",
                "break_minutes":max(0,int(request.data.get("breakMinutes") or 0)),
                "weekly_off":request.data.get("weeklyOff") or [],
                "is_active":bool(request.data.get("isActive",True)),
            }
            if shift:
                for key,value in values.items():
                    setattr(shift,key,value)
                shift.save()
            else:
                shift=Shift.objects.create(company=company,branch=request.user.branch,**values)
            return response.Response({"id":str(shift.id),"name":shift.name})

        shift=Shift.objects.filter(pk=request.data.get("shiftId"),company=company).first()
        if not shift:
            return response.Response({"message":"Shift not found."},status=404)

        employees=Employee.objects.filter(company=company,status="Active")
        team_id=request.data.get("teamId")
        staff_ids=request.data.get("staffIds") or []
        if team_id:
            employees=employees.filter(team_id=team_id)
        elif staff_ids:
            employees=employees.filter(id__in=staff_ids)
        else:
            return response.Response({"message":"Select a team or staff members."},status=400)

        count=employees.update(shift=shift,shift_label=shift.name)
        return response.Response({"assigned":count,"shiftId":str(shift.id)})


class ManagerRulesView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"
    def get(self,request):
        obj=get_attendance_rule(request.user.company,None)
        return response.Response(AttendanceRuleSerializer(obj).data)
    def post(self,request):
        obj=AttendanceRule.objects.filter(
            company=request.user.company,
            branch__isnull=True,
            is_default=True,
        ).first()
        if obj:
            ser=AttendanceRuleSerializer(obj,data=request.data,partial=True)
        else:
            ser=AttendanceRuleSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        obj=ser.save(company=request.user.company,branch=None,is_default=True)
        return response.Response(AttendanceRuleSerializer(obj).data)

class StaffAttendanceDetailsView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"
    def get(self,request,staff_id,date):
        employee=Employee.objects.filter(
            company=request.user.company,
            employee_code=staff_id,
        ).select_related("shift","branch").first()
        if not employee:
            try:
                import uuid
                uuid.UUID(str(staff_id))
                employee=Employee.objects.filter(
                    pk=staff_id,
                    company=request.user.company,
                ).select_related("shift","branch").first()
            except (ValueError,TypeError,AttributeError):
                employee=None
        if not employee:
            return response.Response({"message":"Staff not found."},status=404)
        record=AttendanceRecord.objects.filter(employee=employee,date=date).first()
        ensure_record_session(record)
        leave=LeaveRequest.objects.filter(employee=employee,start_date__lte=date,end_date__gte=date).first()
        correction=PunchCorrection.objects.filter(employee=employee,attendance=record).order_by("-created_at").first() if record else None
        return response.Response({
            "staffInfo":{"id":employee.employee_code,"employeeId":str(employee.id),"name":employee.name,"designation":employee.designation,"branch":employee.branch.name if employee.branch else "","shift":employee.shift_label or (employee.shift.name if employee.shift else ""),"weeklyOff":", ".join(employee.shift.weekly_off) if employee.shift else ""},
            "attendanceSummary":{"date":date,"status":record.status if record else "Absent","workedHours":f"{(record.worked_minutes if record else 0)//60}h {(record.worked_minutes if record else 0)%60}m","lateMinutes":record.late_minutes if record else 0,"earlyExitMinutes":record.early_exit_minutes if record else 0,"missingClockOut":bool(record and record.clock_in and not record.clock_out)},
            "sessions":[
                {
                    "id":str(session.id),
                    "clockIn":session.clock_in,
                    "clockOut":session.clock_out,
                    "duration":session.worked_minutes,
                    "autoClosed":session.auto_closed,
                    "source":session.source,
                    "clockInLocation":session.clock_in_location if get_attendance_rule(request.user.company,None).location_tracking_enabled else {},
                    "clockOutLocation":session.clock_out_location if get_attendance_rule(request.user.company,None).location_tracking_enabled else {},
                }
                for session in (record.sessions.all() if record else [])
            ],
            "locationEnabled":bool(get_attendance_rule(request.user.company,None).location_tracking_enabled),
            "correctionInfo":PunchCorrectionSerializer(correction).data if correction else None,
            "leaveInfo":LeaveRequestSerializer(leave).data if leave else None,
            "auditHistory":[],
        })
