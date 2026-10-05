from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone
from rest_framework import decorators,response,status
from common.viewsets import CompanyScopedModelViewSet
from apps.attendance.models import AttendanceRecord
from apps.employees.models import Employee
from .models import SalaryStructure,SalaryAdvance,PayrollRun,Payslip
from .serializers import *

class SalaryStructureViewSet(CompanyScopedModelViewSet):
    queryset=SalaryStructure.objects.select_related("employee").all(); serializer_class=SalaryStructureSerializer

class SalaryAdvanceViewSet(CompanyScopedModelViewSet):
    queryset=SalaryAdvance.objects.select_related("employee").all(); serializer_class=SalaryAdvanceSerializer
    def perform_create(self,serializer):
        amount=serializer.validated_data["amount"]
        serializer.save(company=self.request.user.company,branch=self.request.user.branch,outstanding_balance=amount)

class PayrollRunViewSet(CompanyScopedModelViewSet):
    queryset=PayrollRun.objects.prefetch_related("payslips").all(); serializer_class=PayrollRunSerializer

    @decorators.action(detail=True,methods=["post"])
    def process(self,request,pk=None):
        run=self.get_object()
        if run.status=="Processed": return response.Response(PayrollRunSerializer(run).data)
        Payslip.objects.filter(payroll_run=run).delete()
        employees=Employee.objects.filter(company=run.company,status="Active")
        for emp in employees:
            structure=SalaryStructure.objects.filter(employee=emp).first()
            if not structure: continue
            attendance=AttendanceRecord.objects.filter(employee=emp,date__year=run.year,date__month=run.month)
            present_days=attendance.exclude(status__iexact="Absent").count()
            overtime=attendance.aggregate(v=Sum("overtime_minutes"))["v"] or 0
            overtime_amount=(Decimal(overtime)/Decimal("60"))*structure.overtime_rate
            advances=SalaryAdvance.objects.filter(employee=emp,status="Active").aggregate(v=Sum("outstanding_balance"))["v"] or Decimal("0")
            recovery=min(advances, max(Decimal("0"),structure.basic*Decimal("0.20")))
            gross=structure.basic+structure.hra+structure.allowances+overtime_amount
            net=max(Decimal("0"),gross-structure.deductions-recovery)
            Payslip.objects.create(company=run.company,branch=emp.branch,payroll_run=run,employee=emp,attendance_days=present_days,overtime_minutes=overtime,basic=structure.basic,allowances=structure.hra+structure.allowances,overtime_amount=overtime_amount,deductions=structure.deductions,advance_recovery=recovery,gross=gross,net=net)
        run.status="Processed"; run.processed_at=timezone.now(); run.processed_by=request.user
        run.save(update_fields=["status","processed_at","processed_by","updated_at"])
        return response.Response(PayrollRunSerializer(run).data)

class PayslipViewSet(CompanyScopedModelViewSet):
    queryset=Payslip.objects.select_related("employee","payroll_run").all(); serializer_class=PayslipSerializer
