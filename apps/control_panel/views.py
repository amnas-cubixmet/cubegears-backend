from functools import wraps

from django.contrib import messages
from django.contrib.auth import login, logout
from django.db.models import F, Q, Sum, Count
from django.db.models.deletion import ProtectedError
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
from .forms import (
    CompanyForm, CustomerForm, EmployeeForm, InvoiceForm, JobForm,
    PanelLoginForm, StockItemForm, SubscriptionForm, UserForm,
)


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


def _display_rows(obj, fields):
    rows = []
    for field_name, label in fields:
        value = getattr(obj, field_name, None)
        if hasattr(value, "all"):
            value = ", ".join(str(item) for item in value.all()) or "—"
        elif value in (None, ""):
            value = "—"
        elif isinstance(value, bool):
            value = "Yes" if value else "No"
        rows.append((label, value))
    return rows


def _save_form(request, form_class, *, instance=None, title, active, cancel_url):
    form = form_class(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        messages.success(request, f"{title} saved successfully.")
        return redirect(cancel_url)
    return render(request, "control_panel/form.html", {
        "page_title": title,
        "active": active,
        "form": form,
        "cancel_url": cancel_url,
        "is_edit": instance is not None,
    })


def _delete_object(request, obj, *, title, active, cancel_url):
    if request.method == "POST":
        try:
            obj.delete()
            messages.success(request, f"{title} deleted successfully.")
            return redirect(cancel_url)
        except ProtectedError:
            messages.error(request, f"{title} cannot be deleted because other records depend on it.")
            return redirect(cancel_url)
    return render(request, "control_panel/confirm_delete.html", {
        "page_title": f"Delete {title}",
        "active": active,
        "object": obj,
        "cancel_url": cancel_url,
    })


@panel_admin_required
def company_create(request):
    return _save_form(request, CompanyForm, title="Add Company", active="companies", cancel_url="panel:companies")


@panel_admin_required
def company_detail(request, pk):
    obj = get_object_or_404(Company, pk=pk)
    return render(request, "control_panel/detail.html", {
        "page_title": "Company Details",
        "active": "companies",
        "object": obj,
        "rows": _display_rows(obj, [
            ("name", "Name"), ("legal_name", "Legal name"), ("slug", "Slug"),
            ("email", "Email"), ("phone", "Phone"), ("gstin", "GSTIN"),
            ("address", "Address"), ("city", "City"), ("state", "State"),
            ("country", "Country"), ("pincode", "Pincode"), ("currency", "Currency"),
            ("plan", "Plan"), ("is_active", "Active"), ("created_at", "Created"),
        ]),
        "edit_url": "panel:company-edit",
        "delete_url": "panel:company-delete",
    })


@panel_admin_required
def company_edit(request, pk):
    obj = get_object_or_404(Company, pk=pk)
    return _save_form(request, CompanyForm, instance=obj, title="Edit Company", active="companies", cancel_url="panel:companies")


@panel_admin_required
def company_delete(request, pk):
    obj = get_object_or_404(Company, pk=pk)
    return _delete_object(request, obj, title="Company", active="companies", cancel_url="panel:companies")


@panel_admin_required
def user_create(request):
    return _save_form(request, UserForm, title="Add User", active="users", cancel_url="panel:users")


@panel_admin_required
def user_detail(request, pk):
    obj = get_object_or_404(User.objects.select_related("company", "branch", "role"), pk=pk)
    return render(request, "control_panel/detail.html", {
        "page_title": "User Details",
        "active": "users",
        "object": obj,
        "rows": _display_rows(obj, [
            ("name", "Name"), ("email", "Email"), ("phone", "Phone"),
            ("company", "Company"), ("branch", "Branch"), ("role", "Role"),
            ("is_active", "Active"), ("is_staff", "Admin staff"),
            ("is_superuser", "Super admin"), ("email_verified", "Email verified"),
            ("created_at", "Created"),
        ]),
        "edit_url": "panel:user-edit",
        "delete_url": "panel:user-delete",
    })


@panel_admin_required
def user_edit(request, pk):
    obj = get_object_or_404(User, pk=pk)
    return _save_form(request, UserForm, instance=obj, title="Edit User", active="users", cancel_url="panel:users")


@panel_admin_required
def user_delete(request, pk):
    obj = get_object_or_404(User, pk=pk)
    if obj == request.user:
        messages.error(request, "You cannot delete your own Control Panel account.")
        return redirect("panel:users")
    return _delete_object(request, obj, title="User", active="users", cancel_url="panel:users")


@panel_admin_required
def subscription_create(request):
    return _save_form(request, SubscriptionForm, title="Add Subscription", active="subscriptions", cancel_url="panel:subscriptions")


@panel_admin_required
def subscription_detail(request, pk):
    obj = get_object_or_404(Subscription.objects.select_related("company", "branch"), pk=pk)
    return render(request, "control_panel/detail.html", {
        "page_title": "Subscription Details", "active": "subscriptions", "object": obj,
        "rows": _display_rows(obj, [
            ("company", "Company"), ("branch", "Branch"), ("plan", "Plan"),
            ("status", "Status"), ("billing_cycle", "Billing cycle"), ("amount", "Amount"),
            ("currency", "Currency"), ("starts_at", "Starts"), ("renews_at", "Renews"),
            ("seats", "Seats"), ("created_at", "Created"),
        ]),
        "edit_url": "panel:subscription-edit", "delete_url": "panel:subscription-delete",
    })


@panel_admin_required
def subscription_edit(request, pk):
    obj = get_object_or_404(Subscription, pk=pk)
    return _save_form(request, SubscriptionForm, instance=obj, title="Edit Subscription", active="subscriptions", cancel_url="panel:subscriptions")


@panel_admin_required
def subscription_delete(request, pk):
    obj = get_object_or_404(Subscription, pk=pk)
    return _delete_object(request, obj, title="Subscription", active="subscriptions", cancel_url="panel:subscriptions")


@panel_admin_required
def customer_create(request):
    return _save_form(request, CustomerForm, title="Add Customer", active="customers", cancel_url="panel:customers")


@panel_admin_required
def customer_detail(request, pk):
    obj = get_object_or_404(Customer.objects.select_related("company", "branch"), pk=pk)
    return render(request, "control_panel/detail.html", {
        "page_title": "Customer Details", "active": "customers", "object": obj,
        "rows": _display_rows(obj, [
            ("company", "Company"), ("branch", "Branch"), ("name", "Name"),
            ("phone", "Phone"), ("whatsapp", "WhatsApp"), ("email", "Email"),
            ("customer_type", "Type"), ("company_name", "Business name"), ("gstin", "GSTIN"),
            ("address", "Address"), ("city", "City"), ("state", "State"), ("pincode", "Pincode"),
            ("credit_limit", "Credit limit"), ("status", "Status"), ("notes", "Notes"),
        ]),
        "edit_url": "panel:customer-edit", "delete_url": "panel:customer-delete",
    })


@panel_admin_required
def customer_edit(request, pk):
    obj = get_object_or_404(Customer, pk=pk)
    return _save_form(request, CustomerForm, instance=obj, title="Edit Customer", active="customers", cancel_url="panel:customers")


@panel_admin_required
def customer_delete(request, pk):
    obj = get_object_or_404(Customer, pk=pk)
    return _delete_object(request, obj, title="Customer", active="customers", cancel_url="panel:customers")


@panel_admin_required
def job_create(request):
    return _save_form(request, JobForm, title="Add Job Card", active="jobs", cancel_url="panel:jobs")


@panel_admin_required
def job_detail(request, pk):
    obj = get_object_or_404(Job.objects.select_related("company", "branch", "customer", "vehicle", "advisor", "technician"), pk=pk)
    return render(request, "control_panel/detail.html", {
        "page_title": "Job Card Details", "active": "jobs", "object": obj,
        "rows": _display_rows(obj, [
            ("job_number", "Job number"), ("company", "Company"), ("branch", "Branch"),
            ("customer", "Customer"), ("vehicle", "Vehicle"), ("advisor", "Advisor"),
            ("technician", "Technician"), ("status", "Status"), ("priority", "Priority"),
            ("odometer", "Odometer"), ("fuel_level", "Fuel level"), ("promised_at", "Promised"),
            ("delivered_at", "Delivered"), ("estimate_total", "Estimate total"),
            ("labour_total", "Labour total"), ("parts_total", "Parts total"), ("notes", "Notes"),
        ]),
        "edit_url": "panel:job-edit", "delete_url": "panel:job-delete",
    })


@panel_admin_required
def job_edit(request, pk):
    obj = get_object_or_404(Job, pk=pk)
    return _save_form(request, JobForm, instance=obj, title="Edit Job Card", active="jobs", cancel_url="panel:jobs")


@panel_admin_required
def job_delete(request, pk):
    obj = get_object_or_404(Job, pk=pk)
    return _delete_object(request, obj, title="Job Card", active="jobs", cancel_url="panel:jobs")


@panel_admin_required
def stock_create(request):
    return _save_form(request, StockItemForm, title="Add Stock Item", active="stock", cancel_url="panel:stock")


@panel_admin_required
def stock_detail(request, pk):
    obj = get_object_or_404(StockItem.objects.select_related("company", "branch", "category", "supplier"), pk=pk)
    return render(request, "control_panel/detail.html", {
        "page_title": "Stock Item Details", "active": "stock", "object": obj,
        "rows": _display_rows(obj, [
            ("name", "Item"), ("company", "Company"), ("branch", "Branch"), ("sku", "SKU"),
            ("barcode", "Barcode"), ("category", "Category"), ("brand", "Brand"),
            ("compatible_vehicle", "Compatible vehicle"), ("unit", "Unit"), ("cost_price", "Cost price"),
            ("selling_price", "Selling price"), ("on_hand", "On hand"), ("reserved", "Reserved"),
            ("minimum_stock", "Minimum stock"), ("reorder_level", "Reorder level"),
            ("rack", "Rack"), ("supplier", "Supplier"), ("tax", "Tax"), ("hsn_code", "HSN"),
            ("status", "Status"),
        ]),
        "edit_url": "panel:stock-edit", "delete_url": "panel:stock-delete",
    })


@panel_admin_required
def stock_edit(request, pk):
    obj = get_object_or_404(StockItem, pk=pk)
    return _save_form(request, StockItemForm, instance=obj, title="Edit Stock Item", active="stock", cancel_url="panel:stock")


@panel_admin_required
def stock_delete(request, pk):
    obj = get_object_or_404(StockItem, pk=pk)
    return _delete_object(request, obj, title="Stock Item", active="stock", cancel_url="panel:stock")


@panel_admin_required
def invoice_create(request):
    return _save_form(request, InvoiceForm, title="Add Invoice / Estimate", active="invoices", cancel_url="panel:invoices")


@panel_admin_required
def invoice_detail(request, pk):
    obj = get_object_or_404(Invoice.objects.select_related("company", "branch", "customer", "vehicle", "job"), pk=pk)
    return render(request, "control_panel/detail.html", {
        "page_title": "Billing Document Details", "active": "invoices", "object": obj,
        "rows": _display_rows(obj, [
            ("number", "Number"), ("company", "Company"), ("branch", "Branch"), ("kind", "Type"),
            ("invoice_type", "Invoice type"), ("status", "Status"), ("date", "Date"),
            ("customer", "Customer"), ("vehicle", "Vehicle"), ("job", "Job"), ("tax_mode", "Tax mode"),
            ("taxable", "Taxable"), ("cgst", "CGST"), ("sgst", "SGST"), ("igst", "IGST"),
            ("discount", "Discount"), ("total", "Total"), ("paid", "Paid"), ("balance", "Balance"),
            ("payment_mode", "Payment mode"), ("payment_terms", "Payment terms"), ("notes", "Notes"),
        ]),
        "edit_url": "panel:invoice-edit", "delete_url": "panel:invoice-delete",
    })


@panel_admin_required
def invoice_edit(request, pk):
    obj = get_object_or_404(Invoice, pk=pk)
    return _save_form(request, InvoiceForm, instance=obj, title="Edit Invoice / Estimate", active="invoices", cancel_url="panel:invoices")


@panel_admin_required
def invoice_delete(request, pk):
    obj = get_object_or_404(Invoice, pk=pk)
    return _delete_object(request, obj, title="Invoice / Estimate", active="invoices", cancel_url="panel:invoices")


@panel_admin_required
def staff_create(request):
    return _save_form(request, EmployeeForm, title="Add Staff", active="staff", cancel_url="panel:staff")


@panel_admin_required
def staff_detail(request, pk):
    obj = get_object_or_404(Employee.objects.select_related("company", "branch", "user", "team", "shift").prefetch_related("skills"), pk=pk)
    return render(request, "control_panel/detail.html", {
        "page_title": "Staff Details", "active": "staff", "object": obj,
        "rows": _display_rows(obj, [
            ("employee_code", "Employee code"), ("name", "Name"), ("company", "Company"),
            ("branch", "Branch"), ("user", "Linked user"), ("phone", "Phone"), ("email", "Email"),
            ("designation", "Designation"), ("role_name", "Role"), ("department_name", "Department"),
            ("team", "Team"), ("shift", "Shift"), ("skills", "Skills"), ("joining_date", "Joining date"),
            ("employment_type", "Employment type"), ("base_salary", "Base salary"),
            ("status", "Status"), ("address", "Address"), ("emergency_contact", "Emergency contact"),
        ]),
        "edit_url": "panel:staff-edit", "delete_url": "panel:staff-delete",
    })


@panel_admin_required
def staff_edit(request, pk):
    obj = get_object_or_404(Employee, pk=pk)
    return _save_form(request, EmployeeForm, instance=obj, title="Edit Staff", active="staff", cancel_url="panel:staff")


@panel_admin_required
def staff_delete(request, pk):
    obj = get_object_or_404(Employee, pk=pk)
    return _delete_object(request, obj, title="Staff", active="staff", cancel_url="panel:staff")
