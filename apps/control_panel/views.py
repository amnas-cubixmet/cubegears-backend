from functools import wraps

from django.contrib.auth import login, logout
from django.db.models import F, Q, Sum, Count
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.employees.models import Employee
from apps.inventory.models import StockItem
from apps.invoices.models import Invoice
from apps.jobs.models import Job
from apps.saas.models import Subscription, StorageUsage, SecurityEvent
from apps.vehicles.models import Vehicle
from .forms import PanelLoginForm


def panel_admin_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/panel/login/?next={request.path}")
        if not (request.user.is_superuser or request.user.is_staff):
            return HttpResponseForbidden("Control Panel access is restricted to CubixGear administrators.")
        return view_func(request, *args, **kwargs)
    return wrapped


def panel_login(request):
    if request.user.is_authenticated:
        if request.user.is_superuser or request.user.is_staff:
            return redirect("panel:dashboard")
        logout(request)

    form = PanelLoginForm(request.POST or None, request=request)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        if not (user.is_superuser or user.is_staff):
            form.add_error(None, "This account does not have Control Panel access.")
        else:
            login(request, user)
            next_url = request.GET.get("next")
            return redirect(next_url or "panel:dashboard")

    return render(request, "control_panel/login.html", {"form": form})


@panel_admin_required
def panel_logout(request):
    logout(request)
    return redirect("panel:login")


def _company_filter(request, queryset):
    company_id = request.GET.get("company", "").strip()
    if company_id:
        queryset = queryset.filter(company_id=company_id)
    return queryset, company_id


def _company_choices():
    return Company.objects.order_by("name").only("id", "name")


@panel_admin_required
def dashboard(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)

    companies = Company.objects.all()
    users = User.objects.all()
    jobs = Job.objects.all()
    invoices = Invoice.objects.filter(kind="invoice")
    subscriptions = Subscription.objects.all()

    active_subscriptions = subscriptions.filter(status__iexact="Active")
    monthly_revenue = active_subscriptions.filter(
        billing_cycle__iexact="monthly"
    ).aggregate(v=Sum("amount"))["v"] or 0
    annual_revenue = active_subscriptions.filter(
        billing_cycle__iexact="annual"
    ).aggregate(v=Sum("amount"))["v"] or 0
    estimated_mrr = monthly_revenue + (annual_revenue / 12)

    context = {
        "page_title": "SaaS Dashboard",
        "active": "dashboard",
        "today": today,
        "total_companies": companies.count(),
        "active_companies": companies.filter(is_active=True).count(),
        "total_users": users.count(),
        "admin_users": users.filter(Q(is_staff=True) | Q(is_superuser=True)).distinct().count(),
        "total_jobs": jobs.count(),
        "open_jobs": jobs.exclude(status=Job.STATUS_DELIVERED).count(),
        "total_invoices": invoices.count(),
        "platform_sales": invoices.aggregate(v=Sum("total"))["v"] or 0,
        "platform_outstanding": invoices.aggregate(v=Sum("balance"))["v"] or 0,
        "active_subscriptions": active_subscriptions.count(),
        "estimated_mrr": estimated_mrr,
        "new_companies_month": companies.filter(created_at__date__gte=month_start).count(),
        "new_users_month": users.filter(created_at__date__gte=month_start).count(),
        "recent_companies": companies.order_by("-created_at")[:7],
        "recent_users": users.select_related("company", "role").order_by("-created_at")[:7],
        "recent_subscriptions": subscriptions.select_related("company").order_by("-created_at")[:7],
        "security_events": SecurityEvent.objects.select_related("company", "user").order_by("-created_at")[:8],
    }
    return render(request, "control_panel/dashboard.html", context)


@panel_admin_required
def companies(request):
    qs = Company.objects.annotate(
        user_count=Count("users", distinct=True),
        branch_count=Count("branches", distinct=True),
    ).order_by("-created_at")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(gstin__icontains=q)
            | Q(city__icontains=q)
        )
    return render(request, "control_panel/companies.html", {
        "page_title": "Companies",
        "active": "companies",
        "rows": qs[:300],
        "q": q,
    })


@panel_admin_required
def company_toggle(request, pk):
    if request.method != "POST":
        return HttpResponseForbidden("POST required.")
    company = get_object_or_404(Company, pk=pk)
    company.is_active = not company.is_active
    company.save(update_fields=["is_active", "updated_at"])
    return redirect(request.POST.get("next") or "panel:companies")


@panel_admin_required
def users(request):
    qs = User.objects.select_related("company", "branch", "role").order_by("-created_at")
    qs, company_id = _company_filter(request, qs)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q))
    return render(request, "control_panel/users.html", {
        "page_title": "Users & Access",
        "active": "users",
        "rows": qs[:400],
        "q": q,
        "company_id": company_id,
        "companies": _company_choices(),
    })


@panel_admin_required
def user_toggle(request, pk):
    if request.method != "POST":
        return HttpResponseForbidden("POST required.")
    user = get_object_or_404(User, pk=pk)
    if user == request.user:
        return HttpResponseForbidden("You cannot disable your own Control Panel account.")
    user.is_active = not user.is_active
    user.save(update_fields=["is_active", "updated_at"])
    return redirect(request.POST.get("next") or "panel:users")


@panel_admin_required
def subscriptions(request):
    qs = Subscription.objects.select_related("company", "branch").order_by("-created_at")
    qs, company_id = _company_filter(request, qs)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(company__name__icontains=q) | Q(plan__icontains=q) | Q(status__icontains=q))
    return render(request, "control_panel/subscriptions.html", {
        "page_title": "Plans & Billing",
        "active": "subscriptions",
        "rows": qs[:300],
        "q": q,
        "company_id": company_id,
        "companies": _company_choices(),
    })


@panel_admin_required
def storage(request):
    qs = StorageUsage.objects.select_related("company").order_by("-date")
    qs, company_id = _company_filter(request, qs)
    totals = qs.aggregate(bytes=Sum("bytes_used"), files=Sum("file_count"))
    return render(request, "control_panel/storage.html", {
        "page_title": "Storage",
        "active": "storage",
        "rows": qs[:300],
        "total_bytes": totals["bytes"] or 0,
        "total_files": totals["files"] or 0,
        "company_id": company_id,
        "companies": _company_choices(),
    })


@panel_admin_required
def security(request):
    qs = SecurityEvent.objects.select_related("company", "user").order_by("-created_at")
    qs, company_id = _company_filter(request, qs)
    return render(request, "control_panel/security.html", {
        "page_title": "Security & Activity",
        "active": "security",
        "rows": qs[:400],
        "company_id": company_id,
        "companies": _company_choices(),
    })


@panel_admin_required
def customers(request):
    qs = Customer.objects.select_related("company", "branch").order_by("-created_at")
    qs, company_id = _company_filter(request, qs)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(phone__icontains=q)
            | Q(email__icontains=q)
            | Q(company_name__icontains=q)
        )
    return render(request, "control_panel/customers.html", {
        "page_title": "All Customers",
        "active": "customers",
        "rows": qs[:400],
        "q": q,
        "company_id": company_id,
        "companies": _company_choices(),
    })


@panel_admin_required
def jobs(request):
    qs = Job.objects.select_related("company", "customer", "vehicle").order_by("-created_at")
    qs, company_id = _company_filter(request, qs)
    status_value = request.GET.get("status", "").strip()
    q = request.GET.get("q", "").strip()
    if status_value:
        qs = qs.filter(status=status_value)
    if q:
        qs = qs.filter(
            Q(job_number__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(vehicle__registration__icontains=q)
            | Q(company__name__icontains=q)
        )
    return render(request, "control_panel/jobs.html", {
        "page_title": "All Job Cards",
        "active": "jobs",
        "rows": qs[:400],
        "status_choices": Job.STATUS_CHOICES,
        "status_value": status_value,
        "q": q,
        "company_id": company_id,
        "companies": _company_choices(),
    })


@panel_admin_required
def stock(request):
    qs = StockItem.objects.select_related("company", "category", "supplier").order_by("company__name", "name")
    qs, company_id = _company_filter(request, qs)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(sku__icontains=q)
            | Q(barcode__icontains=q)
            | Q(company__name__icontains=q)
        )
    return render(request, "control_panel/stock.html", {
        "page_title": "Global Stock",
        "active": "stock",
        "rows": qs[:500],
        "q": q,
        "company_id": company_id,
        "companies": _company_choices(),
    })


@panel_admin_required
def invoices(request):
    qs = Invoice.objects.select_related("company", "customer").order_by("-date", "-created_at")
    qs, company_id = _company_filter(request, qs)
    q = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "").strip()
    if kind:
        qs = qs.filter(kind=kind)
    if q:
        qs = qs.filter(
            Q(number__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(company__name__icontains=q)
        )
    return render(request, "control_panel/invoices.html", {
        "page_title": "Global Billing Documents",
        "active": "invoices",
        "rows": qs[:400],
        "q": q,
        "kind": kind,
        "company_id": company_id,
        "companies": _company_choices(),
    })


@panel_admin_required
def staff(request):
    qs = Employee.objects.select_related("company", "team", "shift").order_by("company__name", "name")
    qs, company_id = _company_filter(request, qs)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(employee_code__icontains=q)
            | Q(phone__icontains=q)
            | Q(company__name__icontains=q)
        )
    return render(request, "control_panel/staff.html", {
        "page_title": "All Workshop Staff",
        "active": "staff",
        "rows": qs[:400],
        "q": q,
        "company_id": company_id,
        "companies": _company_choices(),
    })
