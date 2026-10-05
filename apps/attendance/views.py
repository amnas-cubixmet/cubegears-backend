from datetime import datetime
from django.utils import timezone
from rest_framework import decorators,response,status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from common.viewsets import CompanyScopedModelViewSet
from .models import *
from .serializers import *

class AttendanceRecordViewSet(CompanyScopedModelViewSet):
    queryset=AttendanceRecord.objects.select_related("employee").all(); serializer_class=AttendanceRecordSerializer
class LeaveRequestViewSet(CompanyScopedModelViewSet):
    queryset=LeaveRequest.objects.select_related("employee").all(); serializer_class=LeaveRequestSerializer
class OvertimeRequestViewSet(CompanyScopedModelViewSet):
    queryset=OvertimeRequest.objects.select_related("employee").all(); serializer_class=OvertimeRequestSerializer
class HolidayViewSet(CompanyScopedModelViewSet):
    queryset=Holiday.objects.all(); serializer_class=HolidaySerializer
class AttendanceRuleViewSet(CompanyScopedModelViewSet):
    queryset=AttendanceRule.objects.all(); serializer_class=AttendanceRuleSerializer

class ToggleAttendanceView(APIView):
    permission_classes=[IsAuthenticated]
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
