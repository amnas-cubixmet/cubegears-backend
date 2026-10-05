from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone
from rest_framework import decorators,response,status
from common.viewsets import CompanyScopedModelViewSet
from apps.attendance.models import AttendanceRecord
from apps.employees.models import Employee
from .models import SalaryStructure,SalaryAdvance,PayrollRun,Payslip,Incentive
from .serializers import *

class SalaryStructureViewSet(CompanyScopedModelViewSet):
    queryset=SalaryStructure.objects.select_related("employee").all(); serializer_class=SalaryStructureSerializer

class SalaryAdvanceViewSet(CompanyScopedModelViewSet):
    queryset=SalaryAdvance.objects.select_related("employee").all(); serializer_class=SalaryAdvanceSerializer
    def perform_create(self,serializer):
        amount=serializer.validated_data["amount"]
        serializer.save(company=self.request.user.company,branch=self.request.user.branch,outstanding_balance=amount)

    @decorators.action(detail=True,methods=["post"],url_path="recover")
    def recover(self,request,pk=None):
        adv=self.get_object()
        amount=Decimal(str(request.data.get("amount") or 0))
        if amount<=0 or amount>adv.outstanding_balance:
            return response.Response({"message":"Invalid recovery amount."},status=400)
        adv.recovered_amount += amount
        adv.outstanding_balance -= amount
        if adv.outstanding_balance<=0: adv.status="Fully Recovered"
        adv.save(update_fields=["recovered_amount","outstanding_balance","status","updated_at"])
        return response.Response(SalaryAdvanceSerializer(adv).data)

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

    @decorators.action(detail=True,methods=["post"],url_path="payment")
    def payment(self,request,pk=None):
        slip=self.get_object()
        amount=Decimal(str(request.data.get("amount") or 0))
        outstanding=max(Decimal("0"),slip.net-slip.paid_amount)
        if amount<=0 or amount>outstanding:
            return response.Response({"message":"Payment exceeds outstanding balance or is invalid."},status=400)
        entry={
            "id":f"PMT-{timezone.now().timestamp()}","amount":float(amount),
            "method":request.data.get("method") or "Bank Transfer",
            "reference":request.data.get("reference") or "",
            "date":request.data.get("date") or timezone.localdate().isoformat(),
            "remarks":request.data.get("remarks") or "",
            "recordedBy":request.user.name,
            "transferStatus":request.data.get("transferStatus") or "Successful",
        }
        history=list(slip.payment_history or [])
        history.append(entry)
        if entry["transferStatus"]=="Successful": slip.paid_amount += amount
        slip.payment_history=history
        slip.payment_status="Paid" if slip.paid_amount>=slip.net else ("Partially Paid" if slip.paid_amount>0 else "Unpaid")
        slip.save(update_fields=["paid_amount","payment_history","payment_status","updated_at"])
        return response.Response(PayslipSerializer(slip).data)

class IncentiveViewSet(CompanyScopedModelViewSet):
    queryset=Incentive.objects.select_related("employee","approved_by").all()
    serializer_class=IncentiveSerializer

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
        from apps.employees.models import Employee
        employee_id=self.request.data.get("employee") or self.request.data.get("staffId")
        employee=Employee.objects.get(pk=employee_id,company=self.request.user.company)
        serializer.save(
            company=self.request.user.company,branch=self.request.user.branch,employee=employee,
            incentive_type=self.request.data.get("incentive_type") or self.request.data.get("type") or "Commission",
            completion_date=self.request.data.get("completionDate") or self.request.data.get("completion_date") or timezone.localdate(),
            payroll_month=self.request.data.get("payrollMonth") or "",
        )

    @decorators.action(detail=True,methods=["post"],url_path="status")
    def set_status(self,request,pk=None):
        obj=self.get_object(); obj.status=request.data.get("status") or obj.status
        if obj.status=="Approved":
            obj.approved_by=request.user; obj.approved_at=timezone.now()
        obj.save()
        return response.Response(self.get_serializer(obj).data)
