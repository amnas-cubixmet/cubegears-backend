from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.attendance.models import AttendanceRecord, LeaveRequest, LeaveType, OvertimeRequest
from apps.employees.models import Employee
from apps.jobs.models import Job
from .models import (
    CommissionRule,
    CompensationComponent,
    EmployeeCommission,
    EmployeeCompensationPlan,
    EmployeeWorkLog,
    Incentive,
    JobCardEmployeeAssignment,
    JobWorkSession,
    PayrollAdjustment,
    PayrollPeriod,
    PayrollPolicy,
    PayrollRun,
    Payslip,
    SalaryAdvance,
    SalaryStructure,
)


CENT=Decimal("0.01")


def money(value):
    return Decimal(str(value or 0)).quantize(CENT,rounding=ROUND_HALF_UP)


def month_bounds(year,month):
    last=monthrange(int(year),int(month))[1]
    return date(int(year),int(month),1),date(int(year),int(month),last)


def get_payroll_policy(company,branch=None):
    if branch:
        policy=PayrollPolicy.objects.filter(
            company=company,branch=branch,is_default=True
        ).order_by("-updated_at").first()
        if policy:
            return policy

    policy=PayrollPolicy.objects.filter(
        company=company,branch__isnull=True,is_default=True
    ).order_by("-updated_at").first()
    if policy:
        return policy

    return PayrollPolicy.objects.create(
        company=company,
        branch=None,
        name="Default",
        default_payment_type=PayrollPolicy.PAYMENT_MONTHLY,
        payroll_cycle="monthly",
        working_day_calculation="attendance",
        overtime_rules={"multiplier":1.5},
        commission_rules={"autoApprove":False},
        job_incentive_rules={},
        approval_workflow={"managerApproval":True},
        payment_methods=["Bank Transfer","UPI","Cash","Cheque"],
        unpaid_leave_policy={"monthlyDivisor":30},
        is_default=True,
    )


def get_compensation_plan(employee,as_of=None):
    as_of=as_of or timezone.localdate()
    return EmployeeCompensationPlan.objects.filter(
        company=employee.company,
        employee=employee,
        approval_status__iexact="Approved",
        effective_from__lte=as_of,
    ).filter(
        Q(effective_to__isnull=True)|Q(effective_to__gte=as_of)
    ).order_by("-effective_from","-created_at").first()


def ensure_compensation_plan(employee,as_of=None):
    plan=get_compensation_plan(employee,as_of)
    if plan:
        return plan

    legacy=SalaryStructure.objects.filter(employee=employee).first()
    if legacy:
        rule=legacy.incentive_rule or {}
        salary_basis=str(rule.get("salaryBasis") or "Fixed Monthly").lower()
        mapped={
            "hourly":PayrollPolicy.PAYMENT_HOURLY,
            "daily":PayrollPolicy.PAYMENT_DAILY,
            "commission only":PayrollPolicy.PAYMENT_COMMISSION,
            "fixed monthly":PayrollPolicy.PAYMENT_MONTHLY,
        }.get(salary_basis,PayrollPolicy.PAYMENT_MONTHLY)
        commission_type=(
            EmployeeCompensationPlan.COMMISSION_PERCENTAGE
            if mapped==PayrollPolicy.PAYMENT_COMMISSION
            else EmployeeCompensationPlan.COMMISSION_NONE
        )
        return EmployeeCompensationPlan.objects.create(
            company=employee.company,
            branch=employee.branch,
            employee=employee,
            payment_type=mapped,
            base_salary=legacy.basic,
            daily_wage_rate=money(rule.get("dailyRate")),
            hourly_wage_rate=money(rule.get("hourlyRate")),
            commission_type=commission_type,
            commission_percentage=money(rule.get("commissionPercentage")),
            eligible_revenue_basis=EmployeeCompensationPlan.REVENUE_LABOUR,
            overtime_eligible=True,
            incentive_eligible=True,
            bonus_rules={"fixedAmount":rule.get("fixed",0)},
            applicable_deductions=[
                {"code":"LEGACY","name":"Legacy deductions","amount":float(legacy.deductions)}
            ] if legacy.deductions else [],
            effective_from=legacy.effective_from,
            payment_frequency="monthly",
            approval_status="Approved",
            is_active=True,
            notes="Auto-created from legacy salary structure.",
        )

    policy=get_payroll_policy(employee.company,employee.branch)
    payment_type=employee.payment_type or policy.default_payment_type
    aliases={
        "Monthly Salary":PayrollPolicy.PAYMENT_MONTHLY,
        "Fixed Monthly":PayrollPolicy.PAYMENT_MONTHLY,
        "Daily Wage":PayrollPolicy.PAYMENT_DAILY,
        "Hourly Wage":PayrollPolicy.PAYMENT_HOURLY,
        "Per Job":PayrollPolicy.PAYMENT_PER_JOB,
        "Per Work":PayrollPolicy.PAYMENT_PER_JOB,
        "Fixed Work Charge":PayrollPolicy.PAYMENT_PER_JOB,
        "Commission":PayrollPolicy.PAYMENT_COMMISSION,
        "Commission Only":PayrollPolicy.PAYMENT_COMMISSION,
    }
    payment_type=aliases.get(payment_type,payment_type)
    valid={choice[0] for choice in PayrollPolicy.PAYMENT_TYPE_CHOICES}
    if payment_type not in valid:
        payment_type=policy.default_payment_type

    return EmployeeCompensationPlan.objects.create(
        company=employee.company,
        branch=employee.branch,
        employee=employee,
        payment_type=payment_type,
        base_salary=employee.base_salary if payment_type!=PayrollPolicy.PAYMENT_PER_JOB else Decimal('0'),
        overtime_eligible=payment_type!=PayrollPolicy.PAYMENT_PER_JOB,
        incentive_eligible=payment_type!=PayrollPolicy.PAYMENT_PER_JOB,
        effective_from=employee.joining_date or timezone.localdate(),
        approval_status="Approved",
        is_active=True,
        notes="Auto-created from employee defaults.",
    )


def _leave_days(employee,start,end):
    paid=Decimal("0")
    unpaid=Decimal("0")
    type_map={
        row.name.lower():str(row.leave_type or "Paid").lower()
        for row in LeaveType.objects.filter(company=employee.company,status="Active")
    }

    rows=LeaveRequest.objects.filter(
        company=employee.company,
        employee=employee,
        status__iexact="Approved",
        start_date__lte=end,
        end_date__gte=start,
    )
    for row in rows:
        first=max(row.start_date,start)
        last=min(row.end_date,end)
        if first>last:
            continue
        days=Decimal((last-first).days+1)
        if row.half_day and first==last:
            days=Decimal("0.5")
        leave_kind=type_map.get(str(row.leave_type or "").lower(),"paid")
        if "unpaid" in leave_kind or "lop" in leave_kind:
            unpaid+=days
        else:
            paid+=days
    return paid,unpaid


def attendance_summary(employee,year,month):
    start,end=month_bounds(year,month)
    rows=AttendanceRecord.objects.filter(
        company=employee.company,
        employee=employee,
        date__range=(start,end),
    )
    present=Decimal(rows.exclude(status__iexact="Absent").count())
    worked_minutes=rows.aggregate(value=Sum("worked_minutes"))["value"] or 0
    paid_leave,unpaid_leave=_leave_days(employee,start,end)
    payable_days=present+paid_leave
    payable_hours=(Decimal(worked_minutes)/Decimal("60")).quantize(Decimal("0.01"))

    return {
        "presentDays":present,
        "paidLeaveDays":paid_leave,
        "unpaidLeaveDays":unpaid_leave,
        "payableDays":payable_days,
        "payableHours":payable_hours,
        "workedMinutes":int(worked_minutes),
    }


def overtime_summary(employee,year,month,plan,policy):
    if not plan.overtime_eligible:
        return {"minutes":0,"amount":Decimal("0")}

    rows=OvertimeRequest.objects.filter(
        company=employee.company,
        employee=employee,
        status__iexact="Approved",
        date__year=year,
        date__month=month,
    )
    minutes=rows.aggregate(value=Sum("minutes"))["value"] or 0
    amount=rows.aggregate(value=Sum("amount"))["value"] or Decimal("0")
    amount=money(amount)

    if minutes and amount<=0:
        legacy=SalaryStructure.objects.filter(employee=employee).first()
        policy_rules=policy.overtime_rules or {}
        hourly_rate=(
            money(legacy.overtime_rate) if legacy and legacy.overtime_rate
            else money(plan.hourly_wage_rate)
        )
        if hourly_rate<=0 and plan.base_salary:
            divisor=Decimal(str(policy_rules.get("monthlyHours") or 208))
            hourly_rate=money(plan.base_salary/divisor)
        multiplier=Decimal(str(policy_rules.get("multiplier") or 1))
        amount=money((Decimal(minutes)/Decimal("60"))*hourly_rate*multiplier)

    return {"minutes":int(minutes),"amount":amount}


def sync_job_assignments(employee,start,end):
    if not employee.user_id:
        return

    jobs=Job.objects.filter(
        company=employee.company,
        technician=employee.user,
    ).filter(
        Q(delivered_at__date__range=(start,end))|
        Q(updated_at__date__range=(start,end))
    )

    for job in jobs:
        status="Completed" if job.status==Job.STATUS_DELIVERED else (
            "Approved" if job.status in {Job.STATUS_QC,Job.STATUS_READY} else "Assigned"
        )
        completed_at=job.delivered_at if job.status==Job.STATUS_DELIVERED else None
        assignment,created=JobCardEmployeeAssignment.objects.get_or_create(
            company=employee.company,
            job=job,
            employee=employee,
            defaults={
                "branch":job.branch or employee.branch,
                "role":employee.designation or employee.role_name,
                "completed_at":completed_at,
                "eligible_labour_revenue":money(job.labour_total),
                "eligible_service_revenue":money(job.labour_total),
                "commission_allocation_percent":Decimal("100"),
                "status":status,
                "metadata":{"source":"job.technician"},
            },
        )
        # Never overwrite a manual Job Card allocation or its rate override.
        if not created and (assignment.metadata or {}).get("source")=="job.technician":
            assignment.completed_at=completed_at
            assignment.eligible_labour_revenue=money(job.labour_total)
            assignment.eligible_service_revenue=money(job.labour_total)
            assignment.status=status
            assignment.save(update_fields=[
                "completed_at","eligible_labour_revenue",
                "eligible_service_revenue","status","updated_at",
            ])


def _commission_rule(plan,employee,as_of):
    return CommissionRule.objects.filter(
        company=employee.company,
        employee=employee,
        is_active=True,
        effective_from__lte=as_of,
    ).filter(
        Q(effective_to__isnull=True)|Q(effective_to__gte=as_of)
    ).filter(
        Q(plan=plan)|Q(plan__isnull=True)
    ).order_by("-effective_from","-created_at").first()


def generate_commissions(employee,plan,year,month,policy):
    # Commission remains available only for existing legacy commission plans.
    # New monthly/daily/hourly/per-job workers use wages or fixed work charges.
    legacy_types={
        PayrollPolicy.PAYMENT_COMMISSION,
        PayrollPolicy.PAYMENT_MONTHLY_COMMISSION,
        PayrollPolicy.PAYMENT_DAILY_COMMISSION,
        PayrollPolicy.PAYMENT_HOURLY_COMMISSION,
    }
    if plan.payment_type not in legacy_types:
        return []
    start,end=month_bounds(year,month)
    sync_job_assignments(employee,start,end)

    rules=policy.commission_rules or {}
    eligibility=rules.get("eligibility","job_complete")
    basis_mode=rules.get("basisMode","service_wise")
    assignments=JobCardEmployeeAssignment.objects.select_related("job").filter(
        company=employee.company,
        employee=employee,
    ).filter(
        Q(completed_at__date__range=(start,end))|
        Q(job__delivered_at__date__range=(start,end))|
        Q(job__updated_at__date__range=(start,end))|
        Q(job__invoices__payments__date__range=(start,end))
    ).distinct()

    auto_approve=bool(rules.get("autoApprove",False))
    generated=[]

    for assignment in assignments:
        existing=EmployeeCommission.objects.filter(
            company=employee.company,employee=employee,assignment=assignment,
            payroll_year=year,payroll_month=month,status="Pending",
        )
        if eligibility=="invoice_paid":
            from apps.invoices.models import Invoice
            eligible=Invoice.objects.filter(
                company=employee.company,job=assignment.job,kind="invoice",
                total__gt=0,balance__lte=0,
            ).filter(paid__gte=F("total")).exclude(
                status__in=["Cancelled","Draft"]
            ).exists()
        else:
            eligible=assignment.job.status in {"Ready for Delivery","Delivered"}
        if not eligible or assignment.status not in {"Approved","Completed"}:
            # Pending commission is cancelled when a job reopens. Already
            # approved or paid earnings require an explicit payroll adjustment.
            existing.update(status="Rejected")
            continue
        rule=_commission_rule(plan,employee,end)
        commission_type=rule.commission_type if rule else plan.commission_type
        basis=rule.revenue_basis if rule else plan.eligible_revenue_basis
        percentage=money(rule.percentage if rule else plan.commission_percentage)
        fixed_amount=money(rule.fixed_amount if rule else plan.commission_fixed_amount)
        override=(assignment.metadata or {}).get("commissionRateOverride")
        if override is not None and override!="":
            # Validated in the assignment serializer; a per-job percentage
            # overrides only the rate, never the employee's base wage.
            percentage=money(override)
            commission_type=EmployeeCompensationPlan.COMMISSION_PERCENTAGE
        if commission_type==EmployeeCompensationPlan.COMMISSION_NONE:
            continue
        # Service-wise: approved timesheet revenue is already attributed to
        # this mechanic. Applying a second global split would underpay them.
        # Total-labour mode divides the Job Card's labour revenue by split.
        allocation=money(assignment.commission_allocation_percent)/Decimal("100")
        verified=assignment.work_logs.filter(status="Approved").exists()
        if basis_mode=="service_wise" and verified and basis in {
            EmployeeCompensationPlan.REVENUE_LABOUR,
            EmployeeCompensationPlan.REVENUE_SERVICE,
            EmployeeCompensationPlan.REVENUE_CUSTOM,
        }:
            if basis==EmployeeCompensationPlan.REVENUE_SERVICE:
                base=money(assignment.eligible_service_revenue)
            else:
                base=money(assignment.eligible_labour_revenue)
            allocation=Decimal("1")
        elif basis_mode=="total_labour" and basis in {
            EmployeeCompensationPlan.REVENUE_LABOUR,
            EmployeeCompensationPlan.REVENUE_SERVICE,
        }:
            base=money(assignment.job.labour_total)*allocation
        elif basis==EmployeeCompensationPlan.REVENUE_JOB_CARD:
            base=money(
                assignment.job.estimate_total or
                (assignment.job.labour_total+assignment.job.parts_total)
            )*allocation
        else:
            base=money(
                assignment.eligible_labour_revenue or assignment.job.labour_total
            )*allocation
        minimum=money(rule.minimum_revenue if rule else 0)
        if base<minimum:
            continue

        if commission_type==EmployeeCompensationPlan.COMMISSION_FIXED:
            amount=money(fixed_amount*allocation)
            rate=Decimal("0")
        else:
            amount=money(base*percentage/Decimal("100"))
            rate=percentage

        # A mechanic earns commission once for each Job Card assignment.
        # Period changes or revised salary plans must not create duplicates.
        other=EmployeeCommission.objects.filter(
            company=employee.company,employee=employee,assignment=assignment,
        ).exclude(source_key=f"assignment:{assignment.id}:plan:{plan.id}:{year}-{month:02d}")
        if other.exclude(status="Rejected").exists():
            continue

        source_key=f"assignment:{assignment.id}:plan:{plan.id}:{year}-{month:02d}"
        obj,created=EmployeeCommission.objects.get_or_create(
            company=employee.company,
            source_key=source_key,
            defaults={
                "branch":assignment.branch or employee.branch,
                "employee":employee,
                "plan":plan,
                "assignment":assignment,
                "job":assignment.job,
                "payroll_year":year,
                "payroll_month":month,
                "revenue_basis":basis,
                "eligible_base":base,
                "commission_type":commission_type,
                "rate":rate,
                "fixed_amount":fixed_amount,
                "amount":amount,
                "status":"Approved" if auto_approve else "Pending",
                "approved_at":timezone.now() if auto_approve else None,
                "metadata":{"allocationPercent":float(allocation*100)},
            },
        )
        if not created:
            if obj.status in {"Approved","Paid"}:
                # Approved and settled ledger entries never get silently
                # recalculated after a Job Card or rate changes.
                generated.append(obj)
                continue
            if obj.status=="Rejected":
                obj.status="Pending"
            obj.plan=plan
            obj.assignment=assignment
            obj.job=assignment.job
            obj.revenue_basis=basis
            obj.eligible_base=base
            obj.commission_type=commission_type
            obj.rate=rate
            obj.fixed_amount=fixed_amount
            obj.amount=amount
            obj.metadata={**(obj.metadata or {}),"allocationPercent":float(allocation*100)}
            obj.save(update_fields=[
                "status","plan","assignment","job","revenue_basis","eligible_base",
                "commission_type","rate","fixed_amount","amount","metadata","updated_at",
            ])
        generated.append(obj)

    return generated


def _component_amount(component,base_pay,payable_days,payable_hours):
    if component.calculation_type==CompensationComponent.CALC_PER_DAY:
        return money(component.amount*payable_days)
    if component.calculation_type==CompensationComponent.CALC_PER_HOUR:
        return money(component.amount*payable_hours)
    if component.calculation_type==CompensationComponent.CALC_PERCENT:
        return money(base_pay*component.percentage/Decimal("100"))
    return money(component.amount)


def calculate_employee_payroll(employee,plan,year,month,existing_payslip=None):
    policy=get_payroll_policy(employee.company,employee.branch)
    attendance=attendance_summary(employee,year,month)
    overtime=(
        {'minutes':0,'amount':Decimal('0')}
        if plan.payment_type==PayrollPolicy.PAYMENT_PER_JOB
        else overtime_summary(employee,year,month,plan,policy)
    )
    start,end=month_bounds(year,month)
    generate_commissions(employee,plan,year,month,policy)

    approved_commission=EmployeeCommission.objects.filter(
        company=employee.company,
        employee=employee,
        payroll_year=year,
        payroll_month=month,
        status__iexact="Approved",
    ).aggregate(value=Sum("amount"))["value"] or Decimal("0")
    approved_commission=money(approved_commission)
    if plan.payment_type==PayrollPolicy.PAYMENT_PER_JOB:
        # Fixed worker charges and legacy commissions must never be combined.
        approved_commission=Decimal('0')

    legacy_incentives=Decimal("0")
    if plan.incentive_eligible and plan.payment_type!=PayrollPolicy.PAYMENT_PER_JOB:
        legacy_incentives=money(
            Incentive.objects.filter(
                company=employee.company,
                employee=employee,
                status__iexact="Approved",
                completion_date__range=(start,end),
            ).aggregate(value=Sum("amount"))["value"] or 0
        )

    payment_type=plan.payment_type
    payable_days=attendance["payableDays"]
    payable_hours=attendance["payableHours"]
    hourly_source=(policy.commission_rules or {}).get("hourlyWageSource","attendance")
    if hourly_source=="approved_job_hours":
        approved_minutes=EmployeeWorkLog.objects.filter(
            company=employee.company,employee=employee,status="Approved",
            work_date__range=(start,end),
        ).aggregate(value=Sum("minutes"))["value"] or 0
        payable_hours=(Decimal(approved_minutes)/Decimal("60")).quantize(Decimal("0.01"))
    unpaid_leave=attendance["unpaidLeaveDays"]

    base_pay=Decimal("0")
    work_charge_sessions=[]
    if payment_type==PayrollPolicy.PAYMENT_PER_JOB:
        # Each approved work session belongs to exactly one approval month.
        # Customer-facing labour charges never enter worker payroll.
        work_charge_sessions=list(JobWorkSession.objects.filter(
            company=employee.company,employee=employee,
            status=JobWorkSession.APPROVED,
            reviewed_at__date__range=(start,end),
            worker_charge__gt=0,
        ).select_related("job").order_by("reviewed_at","pk"))
        base_pay=money(sum((entry.worker_charge for entry in work_charge_sessions),Decimal("0")))
    if payment_type in {
        PayrollPolicy.PAYMENT_MONTHLY,
        PayrollPolicy.PAYMENT_MONTHLY_COMMISSION,
        PayrollPolicy.PAYMENT_SALARY_INCENTIVE,
    }:
        divisor=Decimal(str((policy.unpaid_leave_policy or {}).get("monthlyDivisor") or 30))
        leave_deduction=money((plan.base_salary/divisor)*unpaid_leave) if divisor else Decimal("0")
        base_pay=max(Decimal("0"),money(plan.base_salary-leave_deduction))
    elif payment_type in {
        PayrollPolicy.PAYMENT_DAILY,
        PayrollPolicy.PAYMENT_DAILY_COMMISSION,
    }:
        base_pay=money(payable_days*plan.daily_wage_rate)
    elif payment_type in {
        PayrollPolicy.PAYMENT_HOURLY,
        PayrollPolicy.PAYMENT_HOURLY_COMMISSION,
    }:
        base_pay=money(payable_hours*plan.hourly_wage_rate)
    elif payment_type==PayrollPolicy.PAYMENT_HYBRID:
        active_earnings=plan.components.filter(
            is_active=True,kind=CompensationComponent.KIND_EARNING
        ).exists()
        if not active_earnings:
            if plan.base_salary:
                base_pay=money(plan.base_salary)
            elif plan.daily_wage_rate:
                base_pay=money(payable_days*plan.daily_wage_rate)
            elif plan.hourly_wage_rate:
                base_pay=money(payable_hours*plan.hourly_wage_rate)

    line_items=[
        {
            "kind":"earning",
            "code":"BASE",
            "description":plan.get_payment_type_display(),
            "quantity":Decimal("1"),
            "rate":base_pay,
            "amount":base_pay,
            "source_type":"compensation_plan",
            "source_id":str(plan.id),
            "source_key":"base",
        }
    ] if base_pay and payment_type!=PayrollPolicy.PAYMENT_PER_JOB else []
    for entry in work_charge_sessions:
        line_items.append({
            "kind":"earning",
            "code":"WORK_CHARGE",
            "description":f"{entry.service_name} · {entry.job.job_number}",
            "quantity":Decimal("1"),
            "rate":entry.worker_charge,
            "amount":entry.worker_charge,
            "source_type":"job_work_session",
            "source_id":str(entry.pk),
            "source_key":f"work-session:{entry.pk}",
            "metadata":{
                "jobId":str(entry.job_id),
                "customerLabourCharge":str(entry.labour_charge),
            },
        })

    component_earnings=Decimal("0")
    component_deductions=Decimal("0")
    for component in plan.components.filter(is_active=True):
        amount=_component_amount(component,base_pay,payable_days,payable_hours)
        if amount<=0:
            continue
        line_items.append({
            "kind":component.kind,
            "code":component.code,
            "description":component.name,
            "quantity":Decimal("1"),
            "rate":amount,
            "amount":amount,
            "source_type":"compensation_component",
            "source_id":str(component.id),
            "source_key":f"component:{component.id}",
        })
        if component.kind==CompensationComponent.KIND_DEDUCTION:
            component_deductions+=amount
        else:
            component_earnings+=amount

    bonus=Decimal("0")
    bonus_rules=plan.bonus_rules or {}
    if plan.incentive_eligible and plan.payment_type!=PayrollPolicy.PAYMENT_PER_JOB:
        bonus=money(bonus_rules.get("fixedAmount") or bonus_rules.get("fixed") or 0)
        legacy_incentives=money(legacy_incentives+bonus)

    configured_deductions=Decimal("0")
    for index,item in enumerate(plan.applicable_deductions or []):
        if not isinstance(item,dict):
            continue
        amount=money(item.get("amount") or 0)
        if not amount and item.get("percentage"):
            amount=money(base_pay*Decimal(str(item.get("percentage")))/Decimal("100"))
        if amount<=0:
            continue
        configured_deductions+=amount
        line_items.append({
            "kind":"deduction",
            "code":str(item.get("code") or f"DED-{index+1}"),
            "description":str(item.get("name") or "Configured Deduction"),
            "quantity":Decimal("1"),
            "rate":amount,
            "amount":amount,
            "source_type":"compensation_plan",
            "source_id":str(plan.id),
            "source_key":f"deduction:{index}",
        })

    if approved_commission:
        line_items.append({
            "kind":"earning","code":"COMMISSION","description":"Approved Commission",
            "quantity":Decimal("1"),"rate":approved_commission,"amount":approved_commission,
            "source_type":"employee_commission","source_id":f"{year}-{month:02d}",
            "source_key":"approved-commission",
        })
    if legacy_incentives:
        line_items.append({
            "kind":"earning","code":"INCENTIVE","description":"Approved Incentives / Bonus",
            "quantity":Decimal("1"),"rate":legacy_incentives,"amount":legacy_incentives,
            "source_type":"incentive","source_id":f"{year}-{month:02d}",
            "source_key":"approved-incentive",
        })
    if overtime["amount"]:
        line_items.append({
            "kind":"earning","code":"OVERTIME","description":"Approved Overtime",
            "quantity":Decimal(str(round(overtime["minutes"]/60,2))),
            "rate":overtime["amount"],"amount":overtime["amount"],
            "source_type":"overtime","source_id":f"{year}-{month:02d}",
            "source_key":"approved-overtime",
        })

    adjustment_total=Decimal("0")
    if existing_payslip:
        for adjustment in existing_payslip.adjustments.filter(status__iexact="Approved"):
            sign=Decimal("-1") if adjustment.adjustment_type=="deduction" else Decimal("1")
            adjustment_total+=sign*money(adjustment.amount)

    gross=money(
        base_pay+component_earnings+approved_commission+
        legacy_incentives+overtime["amount"]+
        max(Decimal("0"),adjustment_total)
    )

    deductions=money(
        component_deductions+configured_deductions+
        abs(min(Decimal("0"),adjustment_total))
    )

    advances=SalaryAdvance.objects.filter(
        company=employee.company,
        employee=employee,
        status="Active",
    ).aggregate(value=Sum("outstanding_balance"))["value"] or Decimal("0")
    recovery_percent=Decimal(str((policy.approval_workflow or {}).get("advanceRecoveryPercent") or 20))
    recovery=money(min(money(advances),gross*recovery_percent/Decimal("100")))
    net=max(Decimal("0"),money(gross-deductions-recovery))

    return {
        "payment_type":payment_type,
        "attendance_days":attendance["presentDays"],
        "payable_days":payable_days,
        "payable_hours":payable_hours,
        "unpaid_leave_days":unpaid_leave,
        "overtime_minutes":overtime["minutes"],
        "basic":base_pay,
        "allowances":component_earnings,
        "incentives":legacy_incentives,
        "commission_amount":approved_commission,
        "bonus_amount":bonus,
        "overtime_amount":overtime["amount"],
        "deductions":deductions,
        "adjustment_amount":money(adjustment_total),
        "advance_recovery":recovery,
        "gross":gross,
        "net":net,
        "line_items":line_items,
        "snapshot":{
            "planId":str(plan.id),
            "paymentType":payment_type,
            "effectiveDate":plan.effective_from.isoformat(),
            "presentDays":float(attendance["presentDays"]),
            "paidLeaveDays":float(attendance["paidLeaveDays"]),
            "unpaidLeaveDays":float(unpaid_leave),
            "payableDays":float(payable_days),
            "payableHours":float(payable_hours),
            "approvedOvertimeMinutes":overtime["minutes"],
            "approvedCommission":float(approved_commission),
            "approvedFixedWorkCharge":float(base_pay) if payment_type==PayrollPolicy.PAYMENT_PER_JOB else 0,
            "approvedIncentives":float(legacy_incentives),
            "hourlyWageSource":hourly_source,
        },
    }


def ensure_period(run):
    start,end=month_bounds(run.year,run.month)
    period,_=PayrollPeriod.objects.get_or_create(
        company=run.company,
        branch=run.branch,
        month=run.month,
        year=run.year,
        defaults={"starts_on":start,"ends_on":end,"status":"Open"},
    )
    if run.period_id!=period.id:
        run.period=period
        run.save(update_fields=["period","updated_at"])
    return period


def process_payroll_run(run,user):
    if run.locked_at or run.approval_status=="Approved":
        raise ValueError("Approved payroll is locked. Use an audited adjustment instead.")

    ensure_period(run)
    employees=Employee.objects.filter(company=run.company,status="Active")
    if run.branch_id:
        employees=employees.filter(branch=run.branch)

    active_ids=[]
    for employee in employees.select_related("branch","user"):
        plan=ensure_compensation_plan(employee,date(run.year,run.month,monthrange(run.year,run.month)[1]))
        existing=Payslip.objects.filter(payroll_run=run,employee=employee).first()
        calc=calculate_employee_payroll(employee,plan,run.year,run.month,existing)

        preserve={
            "paid_amount":existing.paid_amount if existing else Decimal("0"),
            "payment_status":existing.payment_status if existing else "Unpaid",
            "payment_history":existing.payment_history if existing else [],
        }
        slip,created=Payslip.objects.update_or_create(
            payroll_run=run,
            employee=employee,
            defaults={
                "company":run.company,
                "branch":employee.branch,
                "compensation_plan":plan,
                "payment_type":calc["payment_type"],
                "attendance_days":calc["attendance_days"],
                "payable_days":calc["payable_days"],
                "payable_hours":calc["payable_hours"],
                "unpaid_leave_days":calc["unpaid_leave_days"],
                "overtime_minutes":calc["overtime_minutes"],
                "basic":calc["basic"],
                "allowances":calc["allowances"],
                "incentives":calc["incentives"],
                "commission_amount":calc["commission_amount"],
                "bonus_amount":calc["bonus_amount"],
                "overtime_amount":calc["overtime_amount"],
                "deductions":calc["deductions"],
                "adjustment_amount":calc["adjustment_amount"],
                "advance_recovery":calc["advance_recovery"],
                "gross":calc["gross"],
                "net":calc["net"],
                "status":"Generated",
                "calculation_snapshot":calc["snapshot"],
                **preserve,
            },
        )
        active_ids.append(slip.id)
        slip.line_items.all().delete()
        for item in calc["line_items"]:
            slip.line_items.create(
                company=run.company,
                branch=employee.branch,
                **item,
            )

    Payslip.objects.filter(payroll_run=run).exclude(id__in=active_ids).filter(
        paid_amount=0
    ).delete()

    run.status="Processed"
    run.approval_status="Draft"
    run.processed_at=timezone.now()
    run.processed_by=user
    run.save(update_fields=[
        "status","approval_status","processed_at","processed_by","updated_at",
    ])
    return run
