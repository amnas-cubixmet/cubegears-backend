from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import F, Q, Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.customers.models import Customer
from apps.employees.models import Employee
from apps.expenses.models import Expense
from apps.inventory.models import StockItem
from apps.invoices.models import Invoice
from apps.jobs.models import Job
from apps.vehicles.models import Vehicle
from .forms import PanelLoginForm


def _company(request):
    return getattr(request.user, "company", None)


def panel_login(request):
    if request.user.is_authenticated:
        return redirect("panel:dashboard")

    form = PanelLoginForm(request.POST or None, request=request)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        next_url = request.GET.get("next")
        return redirect(next_url or "panel:dashboard")

    return render(request, "control_panel/login.html", {"form": form})


@login_required(login_url="/panel/login/")
def panel_logout(request):
    logout(request)
    return redirect("panel:login")


@login_required(login_url="/panel/login/")
def dashboard(request):
    company = _company(request)
    if not company:
        return render(request, "control_panel/no_company.html")

    today = timezone.localdate()
    invoice_qs = Invoice.objects.filter(company=company, kind="invoice")
    context = {
        "page_title": "Dashboard",
        "active": "dashboard",
        "customers": Customer.objects.filter(company=company, status="active").count(),
        "vehicles": Vehicle.objects.filter(company=company, status="Active").count(),
        "open_jobs": Job.objects.filter(company=company).exclude(status=Job.STATUS_DELIVERED).count(),
        "delivered_today": Job.objects.filter(
            company=company, status=Job.STATUS_DELIVERED, delivered_at__date=today
        ).count(),
        "sales": invoice_qs.aggregate(v=Sum("total"))["v"] or 0,
        "outstanding": invoice_qs.aggregate(v=Sum("balance"))["v"] or 0,
        "expenses": Expense.objects.filter(company=company).aggregate(v=Sum("amount"))["v"] or 0,
        "low_stock": StockItem.objects.filter(
            company=company, on_hand__lte=F("minimum_stock") + F("reserved")
        ).count(),
        "recent_jobs": Job.objects.filter(company=company).select_related(
            "customer", "vehicle"
        )[:8],
        "recent_invoices": invoice_qs.select_related("customer")[:6],
    }
    return render(request, "control_panel/dashboard.html", context)


@login_required(login_url="/panel/login/")
def customers(request):
    company = _company(request)
    qs = Customer.objects.filter(company=company).order_by("-created_at")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(phone__icontains=q)
            | Q(email__icontains=q)
            | Q(company_name__icontains=q)
        )
    return render(request, "control_panel/customers.html", {
        "page_title": "Customers",
        "active": "customers",
        "rows": qs[:250],
        "q": q,
    })


@login_required(login_url="/panel/login/")
def jobs(request):
    company = _company(request)
    qs = Job.objects.filter(company=company).select_related("customer", "vehicle").order_by("-created_at")
    status_value = request.GET.get("status", "").strip()
    q = request.GET.get("q", "").strip()
    if status_value:
        qs = qs.filter(status=status_value)
    if q:
        qs = qs.filter(
            Q(job_number__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(vehicle__registration__icontains=q)
        )
    return render(request, "control_panel/jobs.html", {
        "page_title": "Job Cards",
        "active": "jobs",
        "rows": qs[:250],
        "status_choices": Job.STATUS_CHOICES,
        "status_value": status_value,
        "q": q,
    })


@login_required(login_url="/panel/login/")
def stock(request):
    company = _company(request)
    qs = StockItem.objects.filter(company=company).select_related("category", "supplier").order_by("name")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(barcode__icontains=q))
    return render(request, "control_panel/stock.html", {
        "page_title": "Stock",
        "active": "stock",
        "rows": qs[:300],
        "q": q,
    })


@login_required(login_url="/panel/login/")
def invoices(request):
    company = _company(request)
    qs = Invoice.objects.filter(company=company).select_related("customer").order_by("-date", "-created_at")
    q = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "").strip()
    if kind:
        qs = qs.filter(kind=kind)
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(customer__name__icontains=q))
    return render(request, "control_panel/invoices.html", {
        "page_title": "Invoices & Estimates",
        "active": "invoices",
        "rows": qs[:250],
        "q": q,
        "kind": kind,
    })


@login_required(login_url="/panel/login/")
def staff(request):
    company = _company(request)
    qs = Employee.objects.filter(company=company).select_related("team", "shift").order_by("name")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(employee_code__icontains=q) | Q(phone__icontains=q))
    return render(request, "control_panel/staff.html", {
        "page_title": "Staff",
        "active": "staff",
        "rows": qs[:250],
        "q": q,
    })
