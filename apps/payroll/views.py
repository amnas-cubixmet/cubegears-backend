from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import decorators, response, status
from rest_framework.exceptions import ValidationError

from common.viewsets import CompanyScopedModelViewSet
from apps.employees.models import Employee
from .models import (
    CommissionRule,
    CompensationComponent,
    EmployeeCommission,
    EmployeeCompensationPlan,
    EmployeeWorkLog,
    Incentive,
    JobCardEmployeeAssignment,
    PayrollAdjustment,
    PayrollLineItem,
    PayrollPeriod,
    PayrollPolicy,
    PayrollRun,
    Payslip,
    SalaryAdvance,
    SalaryPayment,
    SalaryStructure,
)
from .serializers import (
    CommissionRuleSerializer,
    CompensationComponentSerializer,
    EmployeeCommissionSerializer,
    EmployeeCompensationPlanSerializer,
    EmployeeWorkLogSerializer,
    IncentiveSerializer,
    JobCardEmployeeAssignmentSerializer,
    PayrollAdjustmentSerializer,
    PayrollLineItemSerializer,
    PayrollPeriodSerializer,
    PayrollPolicySerializer,
    PayrollRunSerializer,
    PayslipSerializer,
    SalaryAdvanceSerializer,
    SalaryPaymentSerializer,
    SalaryStructureSerializer,
)
from .services import ensure_period, process_payroll_run


def _company_employee(request,employee_id):
    return Employee.objects.get(pk=employee_id,company=request.user.company)


def _close_previous_plans(plan):
    if plan.approval_status!="Approved" or not plan.is_active:
        return
    previous=EmployeeCompensationPlan.objects.filter(
        company=plan.company,
        employee=plan.employee,
        is_active=True,
        approval_status="Approved",
        effective_from__lt=plan.effective_from,
    ).exclude(pk=plan.pk)
    previous.update(effective_to=plan.effective_from-timedelta(days=1))


class PayrollPolicyViewSet(CompanyScopedModelViewSet):
    queryset=PayrollPolicy.objects.all()
    serializer_class=PayrollPolicySerializer
    permission_prefix="payroll"

    def get_queryset(self):
        qs=super().get_queryset()
        branch_id=self.request.query_params.get("branchId")
        if branch_id:
            qs=qs.filter(branch_id=branch_id)
        return qs

    def perform_create(self,serializer):
        branch=serializer.validated_data.get("branch",getattr(self.request.user,"branch",None))
        serializer.save(company=self.request.user.company,branch=branch)


class EmployeeCompensationPlanViewSet(CompanyScopedModelViewSet):
    queryset=EmployeeCompensationPlan.objects.select_related(
        "employee","approved_by"
    ).prefetch_related("components").all()
    serializer_class=EmployeeCompensationPlanSerializer
    permission_prefix="payroll"
    action_permission_map={"approve":"payroll.edit"}

    def get_queryset(self):
        qs=super().get_queryset()
        employee_id=self.request.query_params.get("employee") or self.request.query_params.get("staffId")
        active=self.request.query_params.get("active")
        if employee_id and employee_id not in {"All","all"}:
            qs=qs.filter(employee_id=employee_id)
        if active in {"true","1","yes"}:
            today=timezone.localdate()
            qs=qs.filter(
                is_active=True,
                approval_status__iexact="Approved",
                effective_from__lte=today,
            ).filter(
                Q(effective_to__isnull=True)|Q(effective_to__gte=today)
            )
        return qs

    @transaction.atomic
    def perform_create(self,serializer):
        employee=serializer.validated_data.get("employee")
        if not employee:
            employee_id=self.request.data.get("employee") or self.request.data.get("staffId")
            if not employee_id:
                raise ValidationError({"employee":"Employee is required."})
            employee=_company_employee(self.request,employee_id)
        obj=serializer.save(
            company=self.request.user.company,
            branch=employee.branch,
            employee=employee,
        )
        _close_previous_plans(obj)

    def perform_update(self,serializer):
        obj=self.get_object()
        if obj.payslips.exists():
            raise ValidationError({
                "message":"This compensation plan is already used by payroll. Create a new effective-dated plan instead of modifying payroll history."
            })
        updated=serializer.save()
        _close_previous_plans(updated)

    @decorators.action(detail=True,methods=["post"],url_path="approve")
    @transaction.atomic
    def approve(self,request,pk=None):
        obj=self.get_object()
        if obj.payslips.exists() and obj.approval_status=="Approved":
            return response.Response(self.get_serializer(obj).data)
        obj.approval_status="Approved"
        obj.approved_by=request.user
        obj.approved_at=timezone.now()
        obj.is_active=True
        obj.save(update_fields=[
            "approval_status","approved_by","approved_at","is_active","updated_at"
        ])
        _close_previous_plans(obj)
        return response.Response(self.get_serializer(obj).data)


class CompensationComponentViewSet(CompanyScopedModelViewSet):
    queryset=CompensationComponent.objects.select_related("plan","plan__employee").all()
    serializer_class=CompensationComponentSerializer
    permission_prefix="payroll"

    def get_queryset(self):
        qs=super().get_queryset()
        plan_id=self.request.query_params.get("plan")
        if plan_id:
            qs=qs.filter(plan_id=plan_id)
        return qs

    def perform_create(self,serializer):
        plan=serializer.validated_data["plan"]
        if plan.company_id!=self.request.user.company_id:
            raise ValidationError({"plan":"Compensation plan belongs to another company."})
        if plan.payslips.exists():
            raise ValidationError("Create a new compensation plan before changing used payroll components.")
        serializer.save(company=self.request.user.company,branch=plan.branch)

    def perform_update(self,serializer):
        if self.get_object().plan.payslips.exists():
            raise ValidationError("Create a new compensation plan before changing used payroll components.")
        serializer.save()


class CommissionRuleViewSet(CompanyScopedModelViewSet):
    queryset=CommissionRule.objects.select_related("employee","plan").all()
    serializer_class=CommissionRuleSerializer
    permission_prefix="payroll"

    def get_queryset(self):
        qs=super().get_queryset()
        employee_id=self.request.query_params.get("employee") or self.request.query_params.get("staffId")
        if employee_id and employee_id not in {"All","all"}:
            qs=qs.filter(employee_id=employee_id)
        return qs

    def perform_create(self,serializer):
        employee=serializer.validated_data["employee"]
        if employee.company_id!=self.request.user.company_id:
            raise ValidationError({"employee":"Employee belongs to another company."})
        serializer.save(company=self.request.user.company,branch=employee.branch)


class JobCardEmployeeAssignmentViewSet(CompanyScopedModelViewSet):
    queryset=JobCardEmployeeAssignment.objects.select_related("job","employee").all()
    serializer_class=JobCardEmployeeAssignmentSerializer
    permission_prefix="payroll"

    def get_queryset(self):
        qs=super().get_queryset()
        job_id=self.request.query_params.get("job")
        employee_id=self.request.query_params.get("employee") or self.request.query_params.get("staffId")
        if job_id:
            qs=qs.filter(job_id=job_id)
        if employee_id and employee_id not in {"All","all"}:
            qs=qs.filter(employee_id=employee_id)
        return qs

    def _validate_allocation(self,job,employee,allocation,exclude_id=None):
        qs=JobCardEmployeeAssignment.objects.filter(
            company=self.request.user.company,
            job=job,
        )
        if exclude_id:
            qs=qs.exclude(pk=exclude_id)
        current=qs.aggregate(value=Sum("commission_allocation_percent"))["value"] or Decimal("0")
        if current+Decimal(str(allocation or 0))>Decimal("100"):
            raise ValidationError({
                "commissionAllocationPercent":"Total commission allocation for this Job Card cannot exceed 100%."
            })

    @transaction.atomic
    def perform_create(self,serializer):
        job=serializer.validated_data["job"]
        employee=serializer.validated_data["employee"]
        allocation=serializer.validated_data.get("commission_allocation_percent",Decimal("100"))
        self._validate_allocation(job,employee,allocation)
        serializer.save(
            company=self.request.user.company,
            branch=job.branch or employee.branch,
        )

    @transaction.atomic
    def perform_update(self,serializer):
        obj=self.get_object()
        job=serializer.validated_data.get("job",obj.job)
        employee=serializer.validated_data.get("employee",obj.employee)
        allocation=serializer.validated_data.get("commission_allocation_percent",obj.commission_allocation_percent)
        self._validate_allocation(job,employee,allocation,obj.id)
        serializer.save()


class EmployeeWorkLogViewSet(CompanyScopedModelViewSet):
    queryset=EmployeeWorkLog.objects.select_related(
        "assignment","employee","job","approved_by"
    ).all()
    serializer_class=EmployeeWorkLogSerializer
    permission_prefix="payroll"
    action_permission_map={"approve":"payroll.edit","reject":"payroll.edit"}

    def get_queryset(self):
        qs=super().get_queryset()
        employee_id=self.request.query_params.get("employee") or self.request.query_params.get("staffId")
        job_id=self.request.query_params.get("job")
        if employee_id and employee_id not in {"All","all"}:
            qs=qs.filter(employee_id=employee_id)
        if job_id:
            qs=qs.filter(job_id=job_id)
        return qs

    def perform_create(self,serializer):
        assignment=serializer.validated_data["assignment"]
        serializer.save(
            company=self.request.user.company,
            branch=assignment.branch,
            employee=assignment.employee,
            job=assignment.job,
        )

    def _refresh_assignment(self,assignment):
        approved=assignment.work_logs.filter(status="Approved")
        totals=approved.aggregate(
            minutes=Sum("minutes"),
            labour=Sum("labour_revenue"),
            service=Sum("service_revenue"),
        )
        assignment.approved_work_minutes=totals["minutes"] or 0
        assignment.eligible_labour_revenue=totals["labour"] or Decimal("0")
        assignment.eligible_service_revenue=totals["service"] or Decimal("0")
        assignment.save(update_fields=[
            "approved_work_minutes","eligible_labour_revenue",
            "eligible_service_revenue","updated_at"
        ])

    @decorators.action(detail=True,methods=["post"])
    @transaction.atomic
    def approve(self,request,pk=None):
        obj=self.get_object()
        obj.status="Approved"
        obj.approved_by=request.user
        obj.approved_at=timezone.now()
        obj.save(update_fields=["status","approved_by","approved_at","updated_at"])
        self._refresh_assignment(obj.assignment)
        return response.Response(self.get_serializer(obj).data)

    @decorators.action(detail=True,methods=["post"])
    @transaction.atomic
    def reject(self,request,pk=None):
        obj=self.get_object()
        obj.status="Rejected"
        obj.approved_by=request.user
        obj.approved_at=timezone.now()
        obj.save(update_fields=["status","approved_by","approved_at","updated_at"])
        self._refresh_assignment(obj.assignment)
        return response.Response(self.get_serializer(obj).data)


class EmployeeCommissionViewSet(CompanyScopedModelViewSet):
    queryset=EmployeeCommission.objects.select_related(
        "employee","job","assignment","plan","approved_by"
    ).all()
    serializer_class=EmployeeCommissionSerializer
    permission_prefix="payroll"
    action_permission_map={"set_status":"payroll.edit"}

    def get_queryset(self):
        qs=super().get_queryset()
        employee_id=self.request.query_params.get("employee") or self.request.query_params.get("staffId")
        year=self.request.query_params.get("year")
        month=self.request.query_params.get("month")
        st=self.request.query_params.get("status")
        if employee_id and employee_id not in {"All","all"}:
            qs=qs.filter(employee_id=employee_id)
        if year:
            qs=qs.filter(payroll_year=year)
        if month and str(month).isdigit():
            qs=qs.filter(payroll_month=month)
        if st and st not in {"All","all"}:
            qs=qs.filter(status=st)
        return qs

    @decorators.action(detail=True,methods=["post"],url_path="status")
    def set_status(self,request,pk=None):
        obj=self.get_object()
        next_status=request.data.get("status") or obj.status
        if next_status not in {"Pending","Approved","Rejected","Paid"}:
            raise ValidationError({"status":"Invalid commission status."})
        obj.status=next_status
        if next_status=="Approved":
            obj.approved_by=request.user
            obj.approved_at=timezone.now()
        obj.save(update_fields=["status","approved_by","approved_at","updated_at"])
        return response.Response(self.get_serializer(obj).data)


class SalaryStructureViewSet(CompanyScopedModelViewSet):
    queryset=SalaryStructure.objects.select_related("employee").all()
    serializer_class=SalaryStructureSerializer
    permission_prefix="payroll"


class SalaryAdvanceViewSet(CompanyScopedModelViewSet):
    queryset=SalaryAdvance.objects.select_related("employee").all()
    serializer_class=SalaryAdvanceSerializer
    permission_prefix="payroll"
    action_permission_map={"recover":"payroll.edit"}

    def perform_create(self,serializer):
        amount=serializer.validated_data["amount"]
        serializer.save(
            company=self.request.user.company,
            branch=self.request.user.branch,
            outstanding_balance=amount,
        )

    @decorators.action(detail=True,methods=["post"],url_path="recover")
    def recover(self,request,pk=None):
        adv=self.get_object()
        amount=Decimal(str(request.data.get("amount") or 0))
        if amount<=0 or amount>adv.outstanding_balance:
            return response.Response({"message":"Invalid recovery amount."},status=400)
        adv.recovered_amount+=amount
        adv.outstanding_balance-=amount
        if adv.outstanding_balance<=0:
            adv.status="Fully Recovered"
        adv.save(update_fields=["recovered_amount","outstanding_balance","status","updated_at"])
        return response.Response(SalaryAdvanceSerializer(adv).data)


class PayrollPeriodViewSet(CompanyScopedModelViewSet):
    queryset=PayrollPeriod.objects.all()
    serializer_class=PayrollPeriodSerializer
    permission_prefix="payroll"
    http_method_names=["get","head","options"]


class PayrollRunViewSet(CompanyScopedModelViewSet):
    queryset=PayrollRun.objects.select_related(
        "period","processed_by","approved_by"
    ).prefetch_related(
        "payslips","payslips__line_items","payslips__adjustments","payslips__salary_payments"
    ).all()
    serializer_class=PayrollRunSerializer
    permission_prefix="payroll"
    action_permission_map={
        "process":"payroll.edit",
        "approve":"payroll.edit",
        "submit":"payroll.edit",
    }

    def get_queryset(self):
        qs=super().get_queryset()
        month=self.request.query_params.get("month")
        year=self.request.query_params.get("year")
        branch_id=self.request.query_params.get("branchId")
        if month and str(month).isdigit():
            qs=qs.filter(month=int(month))
        if year and str(year).isdigit():
            qs=qs.filter(year=int(year))
        if branch_id and branch_id not in {"All","all"}:
            qs=qs.filter(branch_id=branch_id)
        return qs

    def perform_create(self,serializer):
        branch=serializer.validated_data.get("branch",getattr(self.request.user,"branch",None))
        run=serializer.save(company=self.request.user.company,branch=branch)
        ensure_period(run)

    @decorators.action(detail=True,methods=["post"])
    @transaction.atomic
    def process(self,request,pk=None):
        run=PayrollRun.objects.select_for_update().get(pk=self.get_object().pk)
        try:
            process_payroll_run(run,request.user)
        except ValueError as exc:
            return response.Response({"message":str(exc)},status=status.HTTP_409_CONFLICT)
        run.refresh_from_db()
        return response.Response(self.get_serializer(run).data)

    @decorators.action(detail=True,methods=["post"])
    def submit(self,request,pk=None):
        run=self.get_object()
        if run.locked_at:
            raise ValidationError("Approved payroll is locked.")
        run.approval_status="Submitted"
        run.save(update_fields=["approval_status","updated_at"])
        return response.Response(self.get_serializer(run).data)

    @decorators.action(detail=True,methods=["post"])
    @transaction.atomic
    def approve(self,request,pk=None):
        run=PayrollRun.objects.select_for_update().get(pk=self.get_object().pk)
        if run.locked_at:
            return response.Response(self.get_serializer(run).data)
        if not run.payslips.exists():
            process_payroll_run(run,request.user)
        now=timezone.now()
        run.status="Approved"
        run.approval_status="Approved"
        run.approved_by=request.user
        run.approved_at=now
        run.locked_at=now
        run.save(update_fields=[
            "status","approval_status","approved_by","approved_at","locked_at","updated_at"
        ])
        run.payslips.update(status="Approved")
        period=ensure_period(run)
        period.status="Locked"
        period.locked_at=now
        period.locked_by=request.user
        period.save(update_fields=["status","locked_at","locked_by","updated_at"])
        return response.Response(self.get_serializer(run).data)


class PayslipViewSet(CompanyScopedModelViewSet):
    queryset=Payslip.objects.select_related(
        "employee","payroll_run","compensation_plan"
    ).prefetch_related("line_items","adjustments","salary_payments").all()
    serializer_class=PayslipSerializer
    permission_prefix="payroll"
    action_permission_map={"payment":"payroll.edit"}

    def get_queryset(self):
        qs=super().get_queryset()
        staff_id=self.request.query_params.get("staffId")
        payment_status=self.request.query_params.get("paymentStatus")
        month_label=self.request.query_params.get("month")
        if staff_id and staff_id not in {"All","all"}:
            qs=qs.filter(employee_id=staff_id)
        if payment_status and payment_status not in {"All","all"}:
            qs=qs.filter(payment_status=payment_status)
        if month_label:
            from calendar import month_name
            for number,name in enumerate(month_name):
                if number and month_label.startswith(name):
                    year=month_label.replace(name,"").strip()
                    if year.isdigit():
                        qs=qs.filter(payroll_run__month=number,payroll_run__year=int(year))
                    break
        return qs

    @decorators.action(detail=True,methods=["post"],url_path="payment")
    @transaction.atomic
    def payment(self,request,pk=None):
        slip=Payslip.objects.select_for_update().get(pk=self.get_object().pk)
        amount=Decimal(str(request.data.get("amount") or 0))
        outstanding=max(Decimal("0"),slip.net-slip.paid_amount)
        if amount<=0 or amount>outstanding:
            return response.Response(
                {"message":"Payment exceeds outstanding balance or is invalid."},
                status=400,
            )

        reference=str(request.data.get("reference") or "").strip()
        if reference and SalaryPayment.objects.filter(
            company=slip.company,payslip=slip,reference=reference
        ).exists():
            return response.Response(
                {"message":"A salary payment with this reference already exists."},
                status=status.HTTP_409_CONFLICT,
            )

        transfer_status=request.data.get("transferStatus") or request.data.get("status") or "Successful"
        payment=SalaryPayment.objects.create(
            company=slip.company,
            branch=slip.branch,
            payslip=slip,
            employee=slip.employee,
            payment_date=request.data.get("date") or timezone.localdate(),
            amount=amount,
            method=request.data.get("method") or "Bank Transfer",
            reference=reference,
            status=transfer_status,
            remarks=request.data.get("remarks") or "",
            recorded_by=request.user,
        )

        history=list(slip.payment_history or [])
        history.append({
            "id":str(payment.id),
            "amount":float(payment.amount),
            "method":payment.method,
            "reference":payment.reference,
            "date":payment.payment_date.isoformat(),
            "remarks":payment.remarks,
            "recordedBy":request.user.name,
            "transferStatus":payment.status,
        })
        if transfer_status=="Successful":
            slip.paid_amount+=amount
        slip.payment_history=history
        slip.payment_status=(
            "Paid" if slip.paid_amount>=slip.net
            else "Partially Paid" if slip.paid_amount>0
            else "Unpaid"
        )
        slip.save(update_fields=["paid_amount","payment_history","payment_status","updated_at"])
        return response.Response(self.get_serializer(slip).data)


class PayrollLineItemViewSet(CompanyScopedModelViewSet):
    queryset=PayrollLineItem.objects.select_related("payslip","payslip__employee").all()
    serializer_class=PayrollLineItemSerializer
    permission_prefix="payroll"
    http_method_names=["get","head","options"]


class PayrollAdjustmentViewSet(CompanyScopedModelViewSet):
    queryset=PayrollAdjustment.objects.select_related("payslip","approved_by").all()
    serializer_class=PayrollAdjustmentSerializer
    permission_prefix="payroll"
    action_permission_map={"approve":"payroll.edit","reject":"payroll.edit"}

    def perform_create(self,serializer):
        slip=serializer.validated_data["payslip"]
        if slip.company_id!=self.request.user.company_id:
            raise ValidationError({"payslip":"Payslip belongs to another company."})
        serializer.save(company=self.request.user.company,branch=slip.branch)

    @decorators.action(detail=True,methods=["post"])
    @transaction.atomic
    def approve(self,request,pk=None):
        obj=PayrollAdjustment.objects.select_for_update().get(pk=self.get_object().pk)
        if obj.status=="Approved":
            return response.Response(self.get_serializer(obj).data)

        slip=Payslip.objects.select_for_update().get(pk=obj.payslip_id)
        amount=abs(Decimal(str(obj.amount)))
        if obj.adjustment_type=="deduction":
            slip.deductions+=amount
            slip.adjustment_amount-=amount
        else:
            slip.gross+=amount
            slip.adjustment_amount+=amount
        slip.net=max(Decimal("0"),slip.gross-slip.deductions-slip.advance_recovery)
        slip.payment_status=(
            "Paid" if slip.paid_amount>=slip.net
            else "Partially Paid" if slip.paid_amount>0
            else "Unpaid"
        )
        slip.save(update_fields=[
            "gross","deductions","adjustment_amount","net","payment_status","updated_at"
        ])

        PayrollLineItem.objects.get_or_create(
            company=slip.company,
            payslip=slip,
            source_key=f"adjustment:{obj.id}",
            defaults={
                "branch":slip.branch,
                "kind":"deduction" if obj.adjustment_type=="deduction" else "earning",
                "code":obj.code,
                "description":obj.description,
                "quantity":Decimal("1"),
                "rate":amount,
                "amount":amount,
                "source_type":"payroll_adjustment",
                "source_id":str(obj.id),
            },
        )

        obj.status="Approved"
        obj.approved_by=request.user
        obj.approved_at=timezone.now()
        obj.save(update_fields=["status","approved_by","approved_at","updated_at"])
        return response.Response(self.get_serializer(obj).data)

    @decorators.action(detail=True,methods=["post"])
    def reject(self,request,pk=None):
        obj=self.get_object()
        if obj.status=="Approved":
            raise ValidationError("Approved adjustments cannot be rejected.")
        obj.status="Rejected"
        obj.approved_by=request.user
        obj.approved_at=timezone.now()
        obj.save(update_fields=["status","approved_by","approved_at","updated_at"])
        return response.Response(self.get_serializer(obj).data)


class SalaryPaymentViewSet(CompanyScopedModelViewSet):
    queryset=SalaryPayment.objects.select_related("payslip","employee","recorded_by").all()
    serializer_class=SalaryPaymentSerializer
    permission_prefix="payroll"
    http_method_names=["get","head","options"]


class IncentiveViewSet(CompanyScopedModelViewSet):
    queryset=Incentive.objects.select_related("employee","approved_by").all()
    serializer_class=IncentiveSerializer
    permission_prefix="payroll"
    action_permission_map={"set_status":"payroll.edit"}

    def get_queryset(self):
        qs=super().get_queryset()
        month=self.request.query_params.get("month")
        staff_id=self.request.query_params.get("staffId")
        st=self.request.query_params.get("status")
        if month:
            qs=qs.filter(payroll_month=month)
        if staff_id and staff_id not in {"All","all"}:
            qs=qs.filter(employee_id=staff_id)
        if st and st not in {"All","all"}:
            qs=qs.filter(status=st)
        return qs

    def perform_create(self,serializer):
        employee_id=self.request.data.get("employee") or self.request.data.get("staffId")
        employee=_company_employee(self.request,employee_id)
        serializer.save(
            company=self.request.user.company,
            branch=employee.branch,
            employee=employee,
            incentive_type=self.request.data.get("incentive_type") or self.request.data.get("type") or "Commission",
            completion_date=self.request.data.get("completionDate") or self.request.data.get("completion_date") or timezone.localdate(),
            payroll_month=self.request.data.get("payrollMonth") or "",
        )

    @decorators.action(detail=True,methods=["post"],url_path="status")
    def set_status(self,request,pk=None):
        obj=self.get_object()
        obj.status=request.data.get("status") or obj.status
        if obj.status=="Approved":
            obj.approved_by=request.user
            obj.approved_at=timezone.now()
        obj.save(update_fields=["status","approved_by","approved_at","updated_at"])
        return response.Response(self.get_serializer(obj).data)
