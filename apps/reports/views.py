from django.db.models import Count,Sum,F
from django.utils import timezone
from apps.accounts.permissions import RolePermission
from rest_framework.response import Response
from rest_framework.views import APIView

class DashboardView(APIView):
    permission_classes=[RolePermission]
    permission_code="dashboard.view"
    def get(self,request):
        company=request.user.company
        from apps.customers.models import Customer
        from apps.vehicles.models import Vehicle
        from apps.jobs.models import Job
        from apps.invoices.models import Invoice
        from apps.expenses.models import Expense
        from apps.inventory.models import StockItem
        today=timezone.localdate()
        invoice_qs=Invoice.objects.filter(company=company,kind="invoice")
        expense_qs=Expense.objects.filter(company=company)
        return Response({
            "customers":Customer.objects.filter(company=company,status="active").count(),
            "vehicles":Vehicle.objects.filter(company=company,status="Active").count(),
            "openJobs":Job.objects.filter(company=company).exclude(status=Job.STATUS_DELIVERED).count(),
            "jobsDeliveredToday":Job.objects.filter(company=company,status=Job.STATUS_DELIVERED,delivered_at__date=today).count(),
            "sales":invoice_qs.aggregate(v=Sum("total"))["v"] or 0,
            "outstanding":invoice_qs.aggregate(v=Sum("balance"))["v"] or 0,
            "expenses":expense_qs.aggregate(v=Sum("amount"))["v"] or 0,
            "lowStock":StockItem.objects.filter(company=company,on_hand__lte=F("minimum_stock")+F("reserved")).count(),
            "recentJobs":list(Job.objects.filter(company=company).values("id","job_number","status","priority","created_at")[:8]),
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
