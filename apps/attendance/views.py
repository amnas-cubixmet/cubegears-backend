from datetime import datetime
from django.utils import timezone
from rest_framework import decorators,response,status
from apps.accounts.permissions import RolePermission
from rest_framework.views import APIView
from common.viewsets import CompanyScopedModelViewSet
from .models import *
from .serializers import *

class AttendanceRecordViewSet(CompanyScopedModelViewSet):
    queryset=AttendanceRecord.objects.select_related("employee").all(); serializer_class=AttendanceRecordSerializer
class LeaveRequestViewSet(CompanyScopedModelViewSet):
    queryset=LeaveRequest.objects.select_related("employee").all(); serializer_class=LeaveRequestSerializer
class OvertimeRequestViewSet(CompanyScopedModelViewSet):
    queryset=OvertimeRequest.objects.select_related("employee","approved_by").all()
    serializer_class=OvertimeRequestSerializer

    def get_queryset(self):
        qs=super().get_queryset()
        month=self.request.query_params.get("month")
        staff_id=self.request.query_params.get("staffId")
        st=self.request.query_params.get("status")
        if month: qs=qs.filter(payroll_month=month)
        if staff_id and staff_id not in {"All","all"}: qs=qs.filter(employee_id=staff_id)
        if st and st not in {"All","all"}: qs=qs.filter(status=st)
        return qs

    def perform_create(self,serializer):
        employee_id=self.request.data.get("employee") or self.request.data.get("staffId")
        from apps.employees.models import Employee
        employee=Employee.objects.get(pk=employee_id,company=self.request.user.company)
        hours=float(self.request.data.get("overtimeHours") or 0)
        minutes=int(self.request.data.get("minutes") or round(hours*60))
        serializer.save(
            company=self.request.user.company,branch=self.request.user.branch,employee=employee,
            minutes=minutes,payroll_month=self.request.data.get("payrollMonth") or "",
            rate=self.request.data.get("rate") or 0,amount=self.request.data.get("amount") or 0,
            audit_history=[{"action":"Overtime Submitted","actor":self.request.user.name,"timestamp":timezone.now().isoformat()}],
        )

    @decorators.action(detail=True,methods=["post"])
    def approve(self,request,pk=None):
        obj=self.get_object()
        obj.status="Approved"; obj.approved_by=request.user; obj.approved_at=timezone.now()
        if request.data.get("rate") is not None: obj.rate=request.data["rate"]
        if request.data.get("amount") is not None: obj.amount=request.data["amount"]
        history=list(obj.audit_history or []); history.insert(0,{"action":"Approved Overtime","actor":request.user.name,"timestamp":timezone.now().isoformat(),"note":request.data.get("managerNote") or ""}); obj.audit_history=history
        obj.save()
        return response.Response(self.get_serializer(obj).data)

    @decorators.action(detail=True,methods=["post"])
    def reject(self,request,pk=None):
        obj=self.get_object(); obj.status="Rejected"; obj.rejection_reason=request.data.get("reason") or "Not authorized"
        history=list(obj.audit_history or []); history.insert(0,{"action":"Rejected Overtime","actor":request.user.name,"timestamp":timezone.now().isoformat(),"reason":obj.rejection_reason}); obj.audit_history=history
        obj.save()
        return response.Response(self.get_serializer(obj).data)

    @decorators.action(detail=True,methods=["post"])
    def cancel(self,request,pk=None):
        obj=self.get_object(); obj.status="Cancelled"
        history=list(obj.audit_history or []); history.insert(0,{"action":"Cancelled Overtime Request","actor":request.user.name,"timestamp":timezone.now().isoformat()}); obj.audit_history=history
        obj.save()
        return response.Response(self.get_serializer(obj).data)
class HolidayViewSet(CompanyScopedModelViewSet):
    queryset=Holiday.objects.all(); serializer_class=HolidaySerializer
class AttendanceRuleViewSet(CompanyScopedModelViewSet):
    queryset=AttendanceRule.objects.all(); serializer_class=AttendanceRuleSerializer

class ToggleAttendanceView(APIView):
    permission_classes=[RolePermission]
    permission_code="attendance.self"
    def post(self,request):
        employee=getattr(request.user,"employee_profile",None)
        if not employee: return response.Response({"message":"No employee profile linked."},status=status.HTTP_400_BAD_REQUEST)
        today=timezone.localdate()
        record,_=AttendanceRecord.objects.get_or_create(company=request.user.company,branch=request.user.branch,employee=employee,date=today)
        if not record.clock_in:
            record.clock_in=timezone.now(); action="clocked_in"
        elif not record.clock_out:
            record.clock_out=timezone.now()
            record.worked_minutes=max(0,int((record.clock_out-record.clock_in).total_seconds()//60))
            rule=AttendanceRule.objects.filter(company=request.user.company,is_default=True).first()
            threshold=rule.overtime_after_minutes if rule else 540
            record.overtime_minutes=max(0,record.worked_minutes-threshold)
            action="clocked_out"
        else:
            return response.Response({"message":"Attendance already completed for today."},status=status.HTTP_400_BAD_REQUEST)
        record.save()
        return response.Response({"action":action,"record":AttendanceRecordSerializer(record).data})
