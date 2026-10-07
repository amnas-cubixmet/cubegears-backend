from datetime import datetime, time
from django.db.models import Q
from django.utils import timezone
from rest_framework import response,status
from apps.accounts.permissions import RolePermission
from rest_framework.views import APIView

from apps.employees.models import Employee
from .models import AttendanceRecord,LeaveRequest,OvertimeRequest,Holiday,AttendanceRule,PunchCorrection,LeaveType
from .serializers import AttendanceRecordSerializer,LeaveRequestSerializer,HolidaySerializer,AttendanceRuleSerializer,PunchCorrectionSerializer,LeaveTypeSerializer
from .services import close_session, get_attendance_rule

def _employee_for_user(user):
    return getattr(user,"employee_profile",None)

class MyAttendanceLogsView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.self"
    def get(self,request):
        employee=_employee_for_user(request.user)
        if not employee: return response.Response([])
        qs=AttendanceRecord.objects.filter(employee=employee).prefetch_related("sessions")
        st=request.query_params.get("status")
        if st and st.upper()!="ALL": qs=qs.filter(status__iexact=st)
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
        events=[{"id":str(x.id),"date":x.date,"type":"Attendance","status":x.status,"title":x.status} for x in records]
        events += [{"id":str(x.id),"date":x.date,"type":"Holiday","status":"Holiday","title":x.name} for x in holidays]
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
        types=LeaveType.objects.filter(company=request.user.company,status="Active")
        result=[]
        for lt in types:
            used=0
            if employee:
                used=LeaveRequest.objects.filter(employee=employee,leave_type=lt.name,status="Approved").count()
            result.append({"id":str(lt.id),"type":lt.name,"allocated":float(lt.annual_allocation),"used":used,"remaining":max(0,float(lt.annual_allocation)-used)})
        return response.Response(result)

class MyLeaveRequestsView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.self"
    def get(self,request):
        employee=_employee_for_user(request.user)
        if not employee: return response.Response([])
        return response.Response(LeaveRequestSerializer(LeaveRequest.objects.filter(employee=employee),many=True).data)
    def post(self,request):
        employee=_employee_for_user(request.user)
        if not employee: return response.Response({"message":"No employee profile linked."},status=400)
        obj=LeaveRequest.objects.create(
            company=request.user.company,branch=request.user.branch,employee=employee,
            leave_type=request.data.get("leaveType") or request.data.get("leave_type") or "Casual Leave",
            start_date=request.data.get("startDate") or request.data.get("start_date"),
            end_date=request.data.get("endDate") or request.data.get("end_date"),
            half_day=bool(request.data.get("halfDay") or False),
            reason=request.data.get("reason") or "",
            attachment=request.data.get("attachment") or "",
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
            rows.append({"id":str(x.id),"type":"Leave","staffId":str(x.employee_id),"staffName":x.employee.name,"status":x.status,"reason":x.reason,"date":x.start_date})
        for x in OvertimeRequest.objects.filter(company=company,status="Pending").select_related("employee"):
            rows.append({"id":str(x.id),"type":"Overtime","staffId":str(x.employee_id),"staffName":x.employee.name,"status":x.status,"reason":x.reason,"date":x.date,"minutes":x.minutes})
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
                obj.reviewed_by=request.user; obj.reviewed_at=timezone.now()
            else:
                obj.approved_by=request.user
            obj.save()
            return response.Response({"id":str(obj.id),"status":obj.status})
        return response.Response({"message":"Approval not found."},status=404)

class ManagerTeamView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"
    def get(self,request):
        qs=AttendanceRecord.objects.filter(company=request.user.company).select_related("employee","branch")
        branch=request.query_params.get("branch"); st=request.query_params.get("status"); role=request.query_params.get("role")
        if branch and branch!="All": qs=qs.filter(branch__name=branch)
        if st and st!="All": qs=qs.filter(status=st)
        if role and role!="All": qs=qs.filter(employee__designation=role)
        rows=[]
        for x in qs[:500]:
            rows.append({"id":str(x.id),"staffId":str(x.employee_id),"name":x.employee.name,"staffName":x.employee.name,"designation":x.employee.designation,"branch":x.branch.name if x.branch else "","date":x.date,"clockIn":x.clock_in,"clockOut":x.clock_out,"workedMinutes":x.worked_minutes,"lateMinutes":x.late_minutes,"overtimeMinutes":x.overtime_minutes,"status":x.status})
        return response.Response(rows)

class ManagerTeamDetailView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"
    def put(self,request,pk):
        obj=AttendanceRecord.objects.filter(pk=pk,company=request.user.company).first()
        if not obj: return response.Response({"message":"Attendance record not found."},status=404)
        for incoming,attr in [("status","status"),("workedMinutes","worked_minutes"),("lateMinutes","late_minutes"),("earlyExitMinutes","early_exit_minutes"),("overtimeMinutes","overtime_minutes")]:
            if incoming in request.data: setattr(obj,attr,request.data[incoming])
        obj.notes=request.data.get("auditReason") or obj.notes
        obj.save()
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
        return response.Response(LeaveTypeSerializer(LeaveType.objects.filter(company=request.user.company),many=True).data)
    def post(self,request):
        data=request.data.copy()
        if "type" not in data: data["type"]="Paid" if data.get("isPaid",True) else "Unpaid"
        ser=LeaveTypeSerializer(data=data); ser.is_valid(raise_exception=True)
        obj=ser.save(company=request.user.company,branch=request.user.branch)
        return response.Response(LeaveTypeSerializer(obj).data,status=201)

class ManagerHolidaysView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.manage"
    def get(self,request):
        return response.Response(HolidaySerializer(Holiday.objects.filter(company=request.user.company),many=True).data)

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
        employee=Employee.objects.filter(Q(pk=staff_id)|Q(employee_code=staff_id),company=request.user.company).select_related("shift","branch").first()
        if not employee: return response.Response({"message":"Staff not found."},status=404)
        record=AttendanceRecord.objects.filter(employee=employee,date=date).first()
        leave=LeaveRequest.objects.filter(employee=employee,start_date__lte=date,end_date__gte=date).first()
        correction=PunchCorrection.objects.filter(employee=employee,attendance=record).order_by("-created_at").first() if record else None
        return response.Response({
            "staffInfo":{"id":str(employee.id),"name":employee.name,"designation":employee.designation,"branch":employee.branch.name if employee.branch else "","shift":employee.shift_label or (employee.shift.name if employee.shift else ""),"weeklyOff":", ".join(employee.shift.weekly_off) if employee.shift else ""},
            "attendanceSummary":{"date":date,"status":record.status if record else "Absent","workedHours":f"{(record.worked_minutes if record else 0)//60}h {(record.worked_minutes if record else 0)%60}m","lateMinutes":record.late_minutes if record else 0,"earlyExitMinutes":record.early_exit_minutes if record else 0,"missingClockOut":bool(record and record.clock_in and not record.clock_out)},
            "sessions":[
                {
                    "id":str(session.id),
                    "clockIn":session.clock_in,
                    "clockOut":session.clock_out,
                    "duration":session.worked_minutes,
                    "autoClosed":session.auto_closed,
                    "source":session.source,
                }
                for session in (record.sessions.all() if record else [])
            ],
            "correctionInfo":PunchCorrectionSerializer(correction).data if correction else None,
            "leaveInfo":LeaveRequestSerializer(leave).data if leave else None,
            "auditHistory":[],
        })
