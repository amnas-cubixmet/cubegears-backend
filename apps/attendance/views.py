from decimal import Decimal, InvalidOperation
import calendar
from datetime import datetime
from django.utils import timezone
from django.db.models import Q
from rest_framework import decorators,response,status
from apps.accounts.permissions import RolePermission
from apps.notifications.models import Notification
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from common.viewsets import CompanyScopedModelViewSet
from apps.employees.services import resolve_employee_for_user
from .models import *
from .serializers import *
from .services import finish_session, get_attendance_rule, get_attendance_state, start_session

class AttendanceRecordViewSet(CompanyScopedModelViewSet):
    queryset=AttendanceRecord.objects.select_related("employee").prefetch_related("sessions").all(); serializer_class=AttendanceRecordSerializer
    permission_code="attendance.manage"
class LeaveRequestViewSet(CompanyScopedModelViewSet):
    queryset=LeaveRequest.objects.select_related("employee").all(); serializer_class=LeaveRequestSerializer
    permission_code="attendance.manage"
class OvertimeRequestViewSet(CompanyScopedModelViewSet):
    queryset=OvertimeRequest.objects.select_related("employee","approved_by").all()
    serializer_class=OvertimeRequestSerializer
    permission_code="attendance.manage"

    def get_queryset(self):
        qs=super().get_queryset()
        month=self.request.query_params.get("month")
        staff_id=self.request.query_params.get("staffId")
        st=self.request.query_params.get("status")
        if month:
            normalized_month=month
            for month_number,month_name in enumerate(calendar.month_name):
                if month_name and month_name.lower() in str(month).lower():
                    year_text="".join(ch for ch in str(month) if ch.isdigit())
                    if len(year_text)>=4:
                        normalized_month=f"{year_text[:4]}-{month_number:02d}"
                    break
            qs=qs.filter(payroll_month=normalized_month)
        if staff_id and staff_id not in {"All","all"}:
            try:
                import uuid
                uuid.UUID(str(staff_id))
                qs=qs.filter(Q(employee_id=staff_id)|Q(employee__employee_code=staff_id))
            except (ValueError,TypeError,AttributeError):
                qs=qs.filter(employee__employee_code=staff_id)
        if st and st not in {"All","all"}: qs=qs.filter(status=st)
        return qs

    def perform_create(self,serializer):
        employee_id=self.request.data.get("employee") or self.request.data.get("staffId")
        from apps.employees.models import Employee

        employee=Employee.objects.filter(
            company=self.request.user.company,
            employee_code=str(employee_id or ""),
        ).first()
        if not employee:
            try:
                employee=Employee.objects.get(pk=employee_id,company=self.request.user.company)
            except Exception as exc:
                raise ValidationError({"staffId":"Employee not found."}) from exc

        hours=float(self.request.data.get("overtimeHours") or 0)
        minutes=int(self.request.data.get("minutes") or round(hours*60))

        payroll_month=self.request.data.get("payrollMonth") or ""
        for month_number,month_name in enumerate(calendar.month_name):
            if month_name and month_name.lower() in str(payroll_month).lower():
                year_text="".join(ch for ch in str(payroll_month) if ch.isdigit())
                if len(year_text)>=4:
                    payroll_month=f"{year_text[:4]}-{month_number:02d}"
                break
        if not payroll_month and self.request.data.get("date"):
            payroll_month=str(self.request.data.get("date"))[:7]

        serializer.save(
            company=self.request.user.company,branch=self.request.user.branch,employee=employee,
            minutes=minutes,payroll_month=payroll_month,
            rate=0,amount=0,
            audit_history=[{"action":"Overtime Submitted","actor":self.request.user.name,"timestamp":timezone.now().isoformat()}],
        )

    @decorators.action(detail=True,methods=["post"])
    def approve(self,request,pk=None):
        obj=self.get_object()

        raw_rate=request.data.get("rate")
        if raw_rate in (None,""):
            from apps.payroll.models import SalaryStructure
            structure=SalaryStructure.objects.filter(employee=obj.employee).first()
            raw_rate=(structure.overtime_rate if structure else obj.rate)

        try:
            rate=Decimal(str(raw_rate or 0))
        except (InvalidOperation,TypeError,ValueError):
            return response.Response({"message":"Enter a valid overtime rate."},status=400)

        if rate<=0:
            return response.Response(
                {"message":"Overtime rate is required before approval."},
                status=400,
            )

        hours=Decimal(obj.minutes)/Decimal("60")
        amount=(hours*rate).quantize(Decimal("0.01"))

        obj.status="Approved"
        obj.approved_by=request.user
        obj.approved_at=timezone.now()
        obj.rate=rate
        obj.amount=amount

        history=list(obj.audit_history or [])
        history.insert(0,{
            "action":"Approved Overtime",
            "actor":request.user.name,
            "timestamp":timezone.now().isoformat(),
            "note":request.data.get("managerNote") or "",
            "rate":float(rate),
            "amount":float(amount),
        })
        obj.audit_history=history
        obj.save(update_fields=[
            "status","approved_by","approved_at","rate","amount",
            "audit_history","updated_at",
        ])

        if obj.employee.user_id:
            Notification.objects.create(
                company=obj.company,
                branch=obj.branch,
                user=obj.employee.user,
                title="Overtime request approved",
                message=f"Your overtime request for {obj.date} was approved for ₹{amount}.",
                notification_type="attendance_request_decision",
                data={
                    "requestId":str(obj.id),
                    "status":"Approved",
                    "rate":float(rate),
                    "amount":float(amount),
                    "route":"/my-attendance/overtime",
                },
            )
        return response.Response(self.get_serializer(obj).data)

    @decorators.action(detail=True,methods=["post"])
    def reject(self,request,pk=None):
        obj=self.get_object(); obj.status="Rejected"; obj.rejection_reason=request.data.get("reason") or "Not authorized"
        history=list(obj.audit_history or []); history.insert(0,{"action":"Rejected Overtime","actor":request.user.name,"timestamp":timezone.now().isoformat(),"reason":obj.rejection_reason}); obj.audit_history=history
        obj.save()
        if obj.employee.user_id:
            Notification.objects.create(
                company=obj.company,
                branch=obj.branch,
                user=obj.employee.user,
                title="Overtime request rejected",
                message=f"Your overtime request for {obj.date} was rejected.",
                notification_type="attendance_request_decision",
                data={"requestId":str(obj.id),"status":"Rejected","route":"/my-attendance/overtime"},
            )
        return response.Response(self.get_serializer(obj).data)

    @decorators.action(detail=True,methods=["post"])
    def cancel(self,request,pk=None):
        obj=self.get_object(); obj.status="Cancelled"
        history=list(obj.audit_history or []); history.insert(0,{"action":"Cancelled Overtime Request","actor":request.user.name,"timestamp":timezone.now().isoformat()}); obj.audit_history=history
        obj.save()
        return response.Response(self.get_serializer(obj).data)
class HolidayViewSet(CompanyScopedModelViewSet):
    queryset=Holiday.objects.all(); serializer_class=HolidaySerializer
    permission_code="attendance.manage"
class AttendanceRuleViewSet(CompanyScopedModelViewSet):
    queryset=AttendanceRule.objects.all(); serializer_class=AttendanceRuleSerializer
    permission_code="attendance.manage"

class AttendanceStatusView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.self"

    def get(self,request):
        employee=resolve_employee_for_user(request.user)
        if not employee:
            rule=get_attendance_rule(request.user.company,request.user.branch)
            return response.Response({
                "employee":None,
                "profileLinked":False,
                "status":"UNAVAILABLE",
                "canCheckIn":False,
                "canCheckOut":False,
                "nextAction":None,
                "reason":"No employee profile is linked to this account.",
                "attendanceMode":rule.attendance_mode,
                "maxSessionsPerDay":rule.max_sessions_per_day,
                "autoCheckoutAt":None,
                "sessionCount":0,
                "rule":AttendanceRuleSerializer(rule).data,
                "record":None,
            })

        state=get_attendance_state(employee)
        rule=state["rule"]
        record=state["record"]

        shift=getattr(employee,"shift",None)
        employee_shift=(
            f"{shift.name} ({shift.start_time.strftime('%H:%M')} - {shift.end_time.strftime('%H:%M')})"
            if shift else employee.shift_label or "Company Default"
        )

        return response.Response({
            "employee":{
                "id":str(employee.id),
                "employeeCode":employee.employee_code,
                "name":employee.name,
                "shiftName":employee_shift,
                "joiningDate":(
                    employee.joining_date
                    or timezone.localtime(employee.created_at).date()
                ),
            },
            "status":"CLOCKED_IN" if state["open_session"] else "CLOCKED_OUT",
            "canCheckIn":state["can_check_in"],
            "canCheckOut":state["can_check_out"],
            "nextAction":state["next_action"],
            "reason":state["reason"],
            "attendanceMode":rule.attendance_mode,
            "maxSessionsPerDay":rule.max_sessions_per_day,
            "autoCheckoutAt":state["auto_checkout_at"],
            "sessionCount":len(state["sessions"]),
            "rule":AttendanceRuleSerializer(rule).data,
            "record":AttendanceRecordSerializer(record).data if record else None,
        })


class ToggleAttendanceView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.self"

    def post(self,request):
        employee=resolve_employee_for_user(request.user)
        if not employee:
            return response.Response({"message":"No employee profile linked."},status=status.HTTP_400_BAD_REQUEST)

        state=get_attendance_state(employee)
        action=request.data.get("action") or state["next_action"]
        location=request.data.get("location") or {}
        source=request.data.get("source") or "web"
        rule=state["rule"]

        if rule.location_required and not location:
            return response.Response(
                {"message":"Location is required for attendance punches."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not rule.location_tracking_enabled and not rule.location_required:
            location={}

        try:
            if action=="check_in":
                session,next_state=start_session(employee,location=location,source=source)
                action_name="clocked_in"
            elif action=="check_out":
                session,next_state=finish_session(employee,location=location,source=source)
                action_name="clocked_out"
            else:
                return response.Response(
                    {"message":state["reason"] or "No attendance action is currently available."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except ValueError as exc:
            return response.Response({"message":str(exc)},status=status.HTTP_400_BAD_REQUEST)

        record=next_state["record"]
        return response.Response({
            "action":action_name,
            "status":"CLOCKED_IN" if next_state["open_session"] else "CLOCKED_OUT",
            "canCheckIn":next_state["can_check_in"],
            "canCheckOut":next_state["can_check_out"],
            "nextAction":next_state["next_action"],
            "reason":next_state["reason"],
            "attendanceMode":next_state["rule"].attendance_mode,
            "autoCheckoutAt":next_state["auto_checkout_at"],
            "sessionCount":len(next_state["sessions"]),
            "session":AttendanceSessionSerializer(session).data,
            "record":AttendanceRecordSerializer(record).data,
        })
