from django.db.models import Count,Sum,F
from django.utils import timezone
from apps.accounts.permissions import RolePermission
from rest_framework.response import Response
from rest_framework.views import APIView

class DashboardView(APIView):
    permission_classes=[RolePermission]
    permission_code="dashboard.view"

    def get(self,request):
        from apps.attendance.models import AttendanceRecord
        from apps.customers.models import Customer
        from apps.employees.models import Employee
        from apps.expenses.models import Expense
        from apps.inventory.models import StockItem
        from apps.invoices.models import Invoice
        from apps.jobs.models import Job
        from apps.payments.models import Payment
        from apps.vehicles.models import Vehicle

        company=request.user.company
        today=timezone.localdate()

        def money(value):
            amount=float(value or 0)
            return f"₹{amount:,.0f}"

        job_qs=Job.objects.filter(company=company).select_related(
            "customer","vehicle","technician","advisor"
        )
        invoice_qs=Invoice.objects.filter(company=company,kind="invoice")
        payment_qs=Payment.objects.filter(company=company,status="Completed")
        expense_qs=Expense.objects.filter(company=company)

        employee=getattr(request.user,"employee_profile",None)
        attendance_record=None
        if employee:
            attendance_record=AttendanceRecord.objects.filter(
                employee=employee,
                date=today,
            ).first()

        attendance_status="CLOCKED_OUT"
        if attendance_record and attendance_record.clock_in and not attendance_record.clock_out:
            attendance_status="CLOCKED_IN"

        todays_vehicle_count=job_qs.filter(
            created_at__date=today
        ).values("vehicle_id").distinct().count()
        if not todays_vehicle_count:
            todays_vehicle_count=Vehicle.objects.filter(company=company,status="Active").count()

        active_statuses=[
            Job.STATUS_NEW,
            Job.STATUS_INSPECTION,
            Job.STATUS_ESTIMATE_PENDING,
            Job.STATUS_APPROVED,
            Job.STATUS_IN_PROGRESS,
            Job.STATUS_WAITING_PARTS,
            Job.STATUS_QC,
        ]

        billed=invoice_qs.aggregate(v=Sum("total"))["v"] or 0
        collected=payment_qs.aggregate(v=Sum("amount"))["v"] or 0
        outstanding=invoice_qs.aggregate(v=Sum("balance"))["v"] or 0
        expenses=expense_qs.aggregate(v=Sum("amount"))["v"] or 0

        booking_jobs=list(
            job_qs.exclude(status=Job.STATUS_DELIVERED)
            .order_by("promised_at","-created_at")[:8]
        )
        bookings=[]
        for job in booking_jobs:
            promised=job.promised_at
            bookings.append({
                "id": job.job_number,
                "time": promised.astimezone(timezone.get_current_timezone()).strftime("%I:%M %p") if promised else "Walk-in",
                "customer": job.customer.name if job.customer else "Customer",
                "vehicle": (
                    f"{job.vehicle.make} {job.vehicle.model} ({job.vehicle.registration})"
                    if job.vehicle else "Vehicle"
                ),
                "service": (job.complaints or ["Workshop Service"])[0],
                "status": job.status,
            })

        recent_jobs=[]
        active_repair=job_qs.filter(status=Job.STATUS_IN_PROGRESS).order_by("-updated_at").first()
        recent_job_rows=[]
        if active_repair:
            recent_job_rows.append(active_repair)
        remaining=job_qs.exclude(status=Job.STATUS_DELIVERED)
        if active_repair:
            remaining=remaining.exclude(pk=active_repair.pk)
        recent_job_rows.extend(list(remaining.order_by("-updated_at")[:5]))

        for job in recent_job_rows:
            recent_jobs.append({
                "id": str(job.id),
                "jobNumber": job.job_number,
                "customer": job.customer.name if job.customer else "",
                "vehicle": (
                    f"{job.vehicle.make} {job.vehicle.model} {job.vehicle.year or ''}".strip()
                    if job.vehicle else ""
                ),
                "registration": job.vehicle.registration if job.vehicle else "",
                "service": (job.complaints or ["Workshop Service"])[0],
                "mechanic": job.technician.name if job.technician else "Unassigned",
                "status": job.status,
                "amount": money(job.estimate_total),
            })

        staff_availability=[]
        for employee_row in Employee.objects.filter(company=company,status="Active").select_related("user")[:6]:
            active_jobs=0
            if employee_row.user_id:
                active_jobs=job_qs.filter(
                    technician=employee_row.user,
                    status__in=active_statuses,
                ).count()
            staff_availability.append({
                "name": employee_row.name,
                "role": employee_row.designation or employee_row.role_name or "Workshop Staff",
                "activeJobs": active_jobs,
                "status": "Active Duty",
                "available": active_jobs < 3,
            })

        stock_alerts=[]
        low_stock_qs=StockItem.objects.filter(
            company=company,
            on_hand__lte=F("minimum_stock")+F("reserved"),
        ).order_by("on_hand")[:6]
        for item in low_stock_qs:
            available=item.available_quantity
            stock_alerts.append({
                "id": str(item.id),
                "partName": item.name,
                "sku": item.sku,
                "currentStock": float(available),
                "minStock": float(item.minimum_stock),
                "priority": "critical" if available <= 0 or available < item.minimum_stock else "warning",
                "message": "Low stock — reorder recommended",
            })

        delivered_today=job_qs.filter(
            status=Job.STATUS_DELIVERED,
            delivered_at__date=today,
        ).count()

        return Response({
            "attendance": {
                "status": attendance_status,
                "clockInTime": (
                    attendance_record.clock_in.astimezone(timezone.get_current_timezone()).strftime("%I:%M %p")
                    if attendance_record and attendance_record.clock_in else None
                ),
                "workedHours": (
                    f"{attendance_record.worked_minutes // 60}h {attendance_record.worked_minutes % 60}m"
                    if attendance_record else "0h 00m"
                ),
            },
            "stats": {
                "todaysVehicles": todays_vehicle_count,
                "ongoingJobs": job_qs.filter(status__in=active_statuses).count(),
                "readyForDelivery": job_qs.filter(status=Job.STATUS_READY).count(),
                "todaysCollection": money(payment_qs.filter(date=today).aggregate(v=Sum("amount"))["v"] or 0),
                "outstandingBalance": money(outstanding),
                "totalRevenue": money(billed),
                "lowStockAlerts": len(stock_alerts),
            },
            "bookings": bookings,
            "jobProgress": {
                "inspection": job_qs.filter(status=Job.STATUS_INSPECTION).count(),
                "awaitingApproval": job_qs.filter(status=Job.STATUS_ESTIMATE_PENDING).count(),
                "inProgress": job_qs.filter(status=Job.STATUS_IN_PROGRESS).count(),
                "waitingForParts": job_qs.filter(status=Job.STATUS_WAITING_PARTS).count(),
                "qualityCheck": job_qs.filter(status=Job.STATUS_QC).count(),
            },
            "deliveries": [
                {
                    "id": job.job_number,
                    "vehicle": job.vehicle.registration if job.vehicle else "",
                    "customer": job.customer.name if job.customer else "",
                    "expectedTime": (
                        job.promised_at.astimezone(timezone.get_current_timezone()).strftime("%I:%M %p %d %b")
                        if job.promised_at else "Today"
                    ),
                    "status": "Ready",
                    "phone": job.customer.phone if job.customer else "",
                }
                for job in job_qs.filter(status=Job.STATUS_READY).order_by("promised_at")[:5]
            ],
            "recentJobs": recent_jobs,
            "payments": {
                "billedAmount": money(billed),
                "receivedPayments": money(collected),
                "unpaidInvoices": invoice_qs.filter(paid=0).exclude(balance=0).count(),
                "partiallyPaidInvoices": invoice_qs.filter(paid__gt=0,balance__gt=0).count(),
                "overdueAmount": money(outstanding),
            },
            "staffAvailability": staff_availability,
            "stockAlerts": stock_alerts,
            "chartData": [],
            "myAttendanceSummary": {
                "todayPunches": (
                    attendance_record.status if attendance_record else "No attendance record"
                ),
                "pendingLeaveRequests": 0,
                "flaggedPunches": 0,
                "monthlyHours": (
                    AttendanceRecord.objects.filter(
                        employee=employee,
                        date__year=today.year,
                        date__month=today.month,
                    ).aggregate(v=Sum("worked_minutes"))["v"] or 0
                    if employee else 0
                ),
                "lateDays": (
                    AttendanceRecord.objects.filter(
                        employee=employee,
                        date__year=today.year,
                        date__month=today.month,
                        late_minutes__gt=0,
                    ).count()
                    if employee else 0
                ),
                "approvedOvertime": (
                    AttendanceRecord.objects.filter(
                        employee=employee,
                        date__year=today.year,
                        date__month=today.month,
                    ).aggregate(v=Sum("overtime_minutes"))["v"] or 0
                    if employee else 0
                ),
            },
            "recentActivity": [],
            "serviceFollowUps": [],
            "summary": {
                "customers": Customer.objects.filter(company=company,status="active").count(),
                "vehicles": Vehicle.objects.filter(company=company,status="Active").count(),
                "jobsDeliveredToday": delivered_today,
                "expenses": money(expenses),
            },
        })

class ReportsView(APIView):
    permission_classes=[RolePermission]
    permission_code="reports.view"
    def get(self,request):
        company=request.user.company
        report_type=request.query_params.get("type","summary")
        from apps.jobs.models import Job
        from apps.invoices.models import Invoice
        from apps.expenses.models import Expense
        from apps.inventory.models import StockItem
        from apps.employees.models import Employee
        if report_type=="sales":
            return Response(list(Invoice.objects.filter(company=company,kind="invoice").values("date").annotate(total=Sum("total")).order_by("-date")[:90]))
        if report_type=="expenses":
            return Response(list(Expense.objects.filter(company=company).values("category").annotate(total=Sum("amount")).order_by("-total")))
        if report_type=="stock":
            return Response(list(StockItem.objects.filter(company=company).values("id","name","sku","on_hand","reserved","minimum_stock","cost_price","selling_price")))
        if report_type=="staff":
            return Response(list(Employee.objects.filter(company=company).values("id","employee_code","name","designation","status")))
        if report_type=="jobs":
            return Response(list(Job.objects.filter(company=company).values("status").annotate(total=Count("id")).order_by("status")))
        return Response({
            "jobs":Job.objects.filter(company=company).count(),
            "sales":Invoice.objects.filter(company=company,kind="invoice").aggregate(v=Sum("total"))["v"] or 0,
            "expenses":Expense.objects.filter(company=company).aggregate(v=Sum("amount"))["v"] or 0,
            "staff":Employee.objects.filter(company=company,status="Active").count(),
        })
