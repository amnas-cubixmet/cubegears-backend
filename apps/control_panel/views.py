from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps

from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from apps.accounts.email import queue_email
from apps.accounts.account_links import password_action_url
from uuid import UUID

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST, require_http_methods
from django.db import transaction, IntegrityError
from django.db.models import F, Q, Sum, Count
from django.db.models.functions import TruncDate
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
from apps.saas.models import Subscription, StorageUsage, StorageEvent, SecurityEvent, SubscriptionPlan, FeatureGroup, PlanFeature, PlanChangeRequest, CustomPlanRequest, RazorpayPlanPayment, MediaFile
from apps.credits.models import FeatureBundle, StorageCreditPolicy, CreditLedger, CreditWallet, BillingCyclePolicy
from apps.credits.billing_cycle_forms import BillingCyclePolicyForm
from apps.credits.storage_forms import StorageCreditPolicyForm
from apps.saas.feature_discovery import sync_existing_modules
from .permissions import route_allowed, landing_route
from .auth_views import _limited
from apps.accounts.permissions import can_delegate, panel_permission
from apps.roles.models import Role
from .audit import record_panel_event
from .forms import (
    BranchForm, CompanyForm, CustomerForm, EmployeeForm, InvoiceForm, JobForm,
    RoleForm, VehicleForm,
    PanelLoginForm, StockItemForm, SubscriptionForm, UserForm, StaffInviteForm,
    PlatformStaffInviteForm,
    PlanBuilderForm, FeatureGroupForm, PlanFeatureForm, FeatureBundleForm,
)


def panel_admin_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), reverse("panel:login"))
        if not route_allowed(request.user, request.resolver_match.url_name):
            return HttpResponseForbidden("Control Panel access is restricted to CubixGear administrators.")
        if request.GET.get("company"):
            try:
                UUID(request.GET["company"].strip())
            except ValueError:
                return HttpResponseBadRequest("Invalid company filter.")
        return view_func(request, *args, **kwargs)
    return wrapped


def _safe_redirect(request, target, fallback):
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(target)
    return redirect(fallback)


def _paginate(request, queryset):
    return Paginator(queryset, 50).get_page(request.GET.get("page"))


def panel_login(request):
    if request.user.is_authenticated:
        destination = landing_route(request.user)
        if destination:
            return redirect("panel:" + destination)
        logout(request)

    form = PanelLoginForm(request.POST if request.method == "POST" else None, request=request)
    if request.method == "POST" and _limited(
        request, str(request.POST.get("email") or "").strip().lower(),
        "login", limit=10, seconds=600,
    ):
        form.add_error(None, "Too many sign-in attempts. Try again in a few minutes.")
    elif request.method == "POST" and form.is_valid():
        user = form.get_user()
        destination = landing_route(user)
        if not destination:
            form.add_error(None, "This account does not have Control Panel access.")
        else:
            login(request, user)
            record_panel_event(request, "PANEL_LOGIN")
            return _safe_redirect(request, request.GET.get("next"), "panel:" + destination)

    return render(request, "control_panel/login.html", {"form": form})


@panel_admin_required
@require_POST
def panel_logout(request):
    record_panel_event(request, "PANEL_LOGOUT")
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
    """Live SaaS overview. No placeholder values or subscription-only KPI cards."""
    today = timezone.localdate()
    month_start = today.replace(day=1)

    companies = Company.objects.all()
    users = User.objects.all()
    jobs = Job.objects.all()
    invoices = Invoice.objects.filter(kind="invoice")
    subscriptions = Subscription.objects.all()

    company_count = companies.count()
    active_company_count = companies.filter(is_active=True).count()
    user_count = users.count()
    admin_count = users.filter(Q(is_staff=True) | Q(is_superuser=True)).distinct().count()
    job_count = jobs.count()
    open_job_count = jobs.exclude(status=Job.STATUS_DELIVERED).count()
    invoice_totals = invoices.aggregate(sales=Sum("total"), outstanding=Sum("balance"))

    # Credits are wallet units, not INR. The credit-first dashboard reports
    # real balances, without pretending that credits are subscription revenue.
    wallet_totals = CreditWallet.objects.aggregate(total=Sum("balance"))
    wallet_count = CreditWallet.objects.count()
    low_wallet_count = CreditWallet.objects.filter(balance__lte=F("low_threshold")).count()

    # Each chart point comes from real company/user registration timestamps.
    # Group in the database: only two aggregate queries for the whole week.
    days = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    company_daily = {
        row["day"]: row["count"]
        for row in companies.filter(created_at__date__gte=days[0])
        .annotate(day=TruncDate("created_at"))
        .values("day").annotate(count=Count("pk"))
    }
    user_daily = {
        row["day"]: row["count"]
        for row in users.filter(created_at__date__gte=days[0])
        .annotate(day=TruncDate("created_at"))
        .values("day").annotate(count=Count("pk"))
    }
    company_values = [company_daily.get(day, 0) for day in days]
    user_values = [user_daily.get(day, 0) for day in days]
    ceiling = max([1, *company_values, *user_values])

    def chart_coordinates(series):
        return [
            (24 + index * 85, round(154 - value * 124 / ceiling, 1))
            for index, value in enumerate(series)
        ]

    def polyline(series):
        return " ".join(f"{x},{y}" for x, y in chart_coordinates(series))

    company_points = polyline(company_values)
    company_area = "M 24,154 L " + " L ".join(
        f"{x},{y}" for x, y in chart_coordinates(company_values)
    ) + " L 534,154 Z"
    chart_days = [
        {"label": day.strftime("%a"), "date": day, "companies": company_values[index],
         "users": user_values[index]}
        for index, day in enumerate(days)
    ]

    active_subscriptions = subscriptions.filter(status__iexact="Active")
    monthly_revenue = active_subscriptions.filter(
        billing_cycle__iexact="monthly"
    ).aggregate(v=Sum("amount"))["v"] or 0
    annual_revenue = active_subscriptions.filter(
        billing_cycle__iexact="annual"
    ).aggregate(v=Sum("amount"))["v"] or 0

    context = {
        "page_title": "Dashboard",
        "active": "dashboard",
        "today": today,
        "total_companies": company_count,
        "active_companies": active_company_count,
        "inactive_companies": company_count - active_company_count,
        "active_company_percent": round(active_company_count * 100 / company_count) if company_count else 0,
        "total_users": user_count,
        "admin_users": admin_count,
        "total_jobs": job_count,
        "open_jobs": open_job_count,
        "total_invoices": invoices.count(),
        "platform_sales": invoice_totals["sales"] or 0,
        "platform_outstanding": invoice_totals["outstanding"] or 0,
        "wallet_credits_total": wallet_totals["total"] or 0,
        "wallet_count": wallet_count,
        "low_wallet_count": low_wallet_count,
        "active_subscriptions": active_subscriptions.count(),
        "estimated_mrr": monthly_revenue + (annual_revenue / 12),
        "new_companies_month": companies.filter(created_at__date__gte=month_start).count(),
        "new_users_month": users.filter(created_at__date__gte=month_start).count(),
        "chart_company_points": company_points,
        "chart_user_points": polyline(user_values),
        "chart_company_area": company_area,
        "chart_days": chart_days,
        "chart_company_total": sum(company_values),
        "chart_user_total": sum(user_values),
        "recent_companies": companies.order_by("-created_at")[:7],
        "recent_users": users.select_related("company", "role").order_by("-created_at")[:7],
        "recent_subscriptions": subscriptions.select_related("company").order_by("-created_at")[:7],
        "recent_credits": CreditLedger.objects.select_related("company").order_by("-created_at")[:6],
        "security_events": SecurityEvent.objects.select_related("company", "user").order_by("-created_at")[:8],
    }
    return render(request, "control_panel/dashboard.html", context)


@panel_admin_required
def companies(request):
    # Platform-wide KPIs are intentionally independent of the active directory filters.
    month_start = timezone.localdate().replace(day=1)
    total_companies = Company.objects.count()
    active_companies = Company.objects.filter(is_active=True).count()
    new_companies_month = Company.objects.filter(created_at__date__gte=month_start).count()

    qs = Company.objects.annotate(
        user_count=Count("users", distinct=True),
        branch_count=Count("branches", distinct=True),
    ).order_by("-created_at")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip().lower()
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(gstin__icontains=q)
            | Q(city__icontains=q)
        )
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)
    else:
        status = ""

    return render(request, "control_panel/companies.html", {
        "page_title": "Companies",
        "active": "companies",
        "rows": _paginate(request, qs),
        "q": q,
        "status": status,
        "total_companies": total_companies,
        "active_companies": active_companies,
        "inactive_companies": total_companies - active_companies,
        "new_companies_month": new_companies_month,
    })


@panel_admin_required
def company_toggle(request, pk):
    company = get_object_or_404(Company, pk=pk)
    if request.method == "GET":
        return render(request, "control_panel/company_toggle.html", {
            "page_title": "Change Company Access",
            "active": "companies",
            "object": company,
        })
    if request.method != "POST":
        return HttpResponseBadRequest("Only GET and POST are supported.")
    company.is_active = not company.is_active
    company.save(update_fields=["is_active", "updated_at"])
    messages.success(request, f"{company.name} access {'enabled' if company.is_active else 'disabled'}.")
    return _safe_redirect(request, request.POST.get("next"), "panel:companies")


@panel_admin_required
def users(request):
    # Summary reflects platform-wide accounts; directory filters affect only rows.
    all_users = User.objects.all()
    total_users = all_users.count()
    active_users = all_users.filter(is_active=True).count()
    admin_users = all_users.filter(Q(is_staff=True) | Q(is_superuser=True)).count()
    verified_users = all_users.filter(email_verified=True).count()

    qs = User.objects.select_related("company", "branch", "role").order_by("-created_at")
    qs, company_id = _company_filter(request, qs)
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip().lower()
    access = request.GET.get("access", "").strip().lower()

    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q))
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "disabled":
        qs = qs.filter(is_active=False)
    else:
        status = ""

    if access == "superuser":
        qs = qs.filter(is_superuser=True)
    elif access == "admin":
        qs = qs.filter(is_staff=True, is_superuser=False)
    elif access == "workshop":
        qs = qs.filter(is_staff=False, is_superuser=False)
    else:
        access = ""

    return render(request, "control_panel/users.html", {
        "page_title": "Users & Access",
        "active": "users",
        "rows": _paginate(request, qs),
        "q": q,
        "status": status,
        "access": access,
        "company_id": company_id,
        "companies": _company_choices(),
        "total_users": total_users,
        "active_users": active_users,
        "admin_users": admin_users,
        "verified_users": verified_users,
    })


@panel_admin_required
def user_toggle(request, pk):
    user = get_object_or_404(User, pk=pk)
    if not _can_manage_account(request.user, user):
        return HttpResponseForbidden("Only a superadmin can manage a superadmin account.")
    if user == request.user:
        return HttpResponseForbidden("You cannot disable your own Control Panel account.")
    if request.method == "GET":
        return render(request, "control_panel/user_toggle.html", {
            "page_title": "Change User Access",
            "active": "users",
            "object": user,
        })
    if request.method != "POST":
        return HttpResponseBadRequest("Only GET and POST are supported.")
    user.is_active = not user.is_active
    user.save(update_fields=["is_active", "updated_at"])
    messages.success(request, f"Account access {'enabled' if user.is_active else 'disabled'} for {user.email}.")
    return _safe_redirect(request, request.POST.get("next"), "panel:users")


@panel_admin_required
def user_invite(request, pk):
    """Confirm on GET; send secure access email only on explicit POST."""
    user = get_object_or_404(
        User.objects.select_related("company", "role"), pk=pk,
    )
    if not _can_manage_account(request.user, user):
        return HttpResponseForbidden("You cannot invite this account.")
    if request.method == "GET":
        return render(request, "control_panel/user_invite.html", {
            "page_title": "Send Account Invitation",
            "active": "users",
            "object": user,
        })
    if request.method != "POST":
        return HttpResponseBadRequest("Only GET and POST are supported.")
    if _send_new_user_invite(request, user):
        messages.success(request, "Account access email scheduled for the registered address.")
    else:
        messages.warning(request, "No email sent. Verify the account, role and active status.")
    return _safe_redirect(request, request.POST.get("next"), "panel:users")


@panel_admin_required
def subscriptions(request):
    # Platform totals do not change when a user filters the directory.
    all_subscriptions = Subscription.objects.all()
    now = timezone.now()
    month_ahead = now + timedelta(days=30)
    total_subscriptions = all_subscriptions.count()
    active_subscriptions = all_subscriptions.filter(status__iexact="active").count()
    upcoming_renewals = all_subscriptions.filter(
        status__iexact="active", renews_at__gte=now, renews_at__lte=month_ahead
    ).count()
    subscriber_companies = all_subscriptions.values("company_id").distinct().count()
    status_options = sorted(
        (s for s in all_subscriptions.order_by().values_list("status", flat=True).distinct() if s),
        key=str.casefold,
    )

    qs = all_subscriptions.select_related("company", "branch", "plan_definition").order_by("-created_at")
    qs, company_id = _company_filter(request, qs)
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    cycle = request.GET.get("cycle", "").strip().lower()

    if q:
        qs = qs.filter(
            Q(company__name__icontains=q) | Q(plan__icontains=q)
            | Q(status__icontains=q)
        )
    if status:
        if any(value.casefold() == status.casefold() for value in status_options):
            qs = qs.filter(status__iexact=status)
        else:
            status = ""
    if cycle in ("monthly", "yearly", "annual"):
        qs = qs.filter(billing_cycle__iexact=cycle)
    else:
        cycle = ""

    return render(request, "control_panel/subscriptions.html", {
        "page_title": "Plans & Billing",
        "active": "subscriptions",
        "rows": _paginate(request, qs),
        "q": q,
        "status": status,
        "cycle": cycle,
        "status_options": status_options,
        "company_id": company_id,
        "companies": _company_choices(),
        "total_subscriptions": total_subscriptions,
        "active_subscriptions": active_subscriptions,
        "upcoming_renewals": upcoming_renewals,
        "subscriber_companies": subscriber_companies,
    })


@panel_admin_required
def subscription_payments(request):
    """Read-only gateway ledger, distinct from net bank settlements/tax invoices."""
    from decimal import Decimal
    rows = RazorpayPlanPayment.objects.select_related("company", "plan").order_by("-created_at")
    rows, company_id = _company_filter(request, rows)
    q = request.GET.get("q", "").strip()
    if q:
        rows = rows.filter(
            Q(company__name__icontains=q) | Q(plan__name__icontains=q)
            | Q(receipt__icontains=q) | Q(razorpay_order_id__icontains=q)
            | Q(razorpay_payment_id__icontains=q)
        )
    selected_status = request.GET.get("status", "")
    if selected_status in {"pending", "captured", "failed", "refunded"}:
        rows = rows.filter(status=selected_status)
    captured = rows.filter(status="captured")
    gross_paise = captured.aggregate(total=Sum("amount_paise"))["total"] or 0
    return render(request, "control_panel/subscription_payments.html", {
        "page_title": "Razorpay Payment History",
        "active": "subscriptions",
        "rows": _paginate(request, rows),
        "company_id": company_id,
        "companies": _company_choices(),
        "q": q,
        "selected_status": selected_status,
        "captured_count": captured.count(),
        "captured_gross_inr": Decimal(gross_paise) / Decimal(100),
    })


@panel_admin_required
def storage(request):
    """Storage rate controls and live previews; page views never debit a wallet."""
    policy = StorageCreditPolicy.objects.order_by("pk").first()
    cycle_policy = BillingCyclePolicy.objects.order_by("pk").first()
    can_edit_pricing = panel_permission(request.user, "storage", "edit")
    can_edit_cycle = panel_permission(request.user, "credits", "edit")
    is_settings_page = request.resolver_match.url_name == "billing-settings"
    save_destination = "panel:billing-settings" if is_settings_page else "panel:storage"
    from apps.credits.cycle_panel import CreditCyclePolicyForm, save_credit_cycle_policy
    from apps.credits.models import CreditCyclePolicy, CreditCycleInvoice

    action = request.POST.get("action", "storage_pricing") if request.method == "POST" else ""
    cycle_action = action == "billing_cycle"
    legacy_action = action == "legacy_credit_cycle"
    cycle_form = BillingCyclePolicyForm(
        request.POST if cycle_action else None, instance=cycle_policy,
    )
    legacy_policy = CreditCyclePolicy.objects.order_by("pk").first()
    legacy_cycle_form = CreditCyclePolicyForm(
        request.POST if legacy_action else None, instance=legacy_policy,
    )
    if request.method == "POST":
        if action not in ("storage_pricing", "billing_cycle", "legacy_credit_cycle"):
            return HttpResponseBadRequest("Unknown billing settings action.")
        if (cycle_action or legacy_action) and not can_edit_cycle:
            return HttpResponseForbidden("Platform credit policy edit permission required.")
        if action == "storage_pricing" and not can_edit_pricing:
            return HttpResponseForbidden("Platform storage pricing edit permission required.")

    if legacy_action:
        if legacy_cycle_form.is_valid():
            saved_legacy = save_credit_cycle_policy(legacy_cycle_form)
            record_panel_event(request, 'LEGACY_CREDIT_CYCLE_POLICY_UPDATED', {
                'enabled': saved_legacy.enabled, 'day_of_month': saved_legacy.day_of_month,
                'block_overdue': saved_legacy.block_overdue,
            })
            messages.success(request, "Monthly credit cycle saved; no wallet debit occurred.")
            return redirect(reverse("panel:billing-settings") + "#legacy-credit-cycle")

    if cycle_action:
        if cycle_form.is_valid():
            with transaction.atomic():
                policy_record = cycle_form.save()
                record_panel_event(request, 'CALENDAR_BILLING_POLICY_UPDATED', {
                    'enabled': policy_record.enabled,
                    'due_day': policy_record.due_day,
                    'due_time': policy_record.due_time.strftime('%H:%M'),
                    'calculation_interval_seconds': policy_record.calculation_interval_seconds,
                    'storage_tracking_interval_seconds': policy_record.storage_tracking_interval_seconds,
                })
            messages.success(request, "Calendar billing policy saved. Existing finalized invoices keep their original due date.")
            return redirect(save_destination)
    policy_form = StorageCreditPolicyForm(
        request.POST if request.method == "POST" and action == "storage_pricing" else None, instance=policy,
    )
    if request.method == "POST" and action == "storage_pricing" and policy_form.is_valid():
        with transaction.atomic():
            saved = policy_form.save()
            # Keep a single effective published rule, including for older databases
            # which may already have multiple policy records.
            StorageCreditPolicy.objects.exclude(pk=saved.pk).update(is_active=False)
            record_panel_event(request, 'STORAGE_PRICE_UPDATED', {
                'credits_per_gb': str(saved.credits_per_gb),
                'included_gb': str(saved.included_gb),
                'billing_mode': saved.billing_mode,
                'billing_period': saved.billing_period,
                'meter_interval_seconds': saved.meter_interval_seconds,
                'active': saved.is_active,
            })
        messages.success(
            request,
            "Storage per-GB rate saved. Wallets are only charged by the scheduled storage billing command.",
        )
        return redirect(save_destination)

    if is_settings_page:
        return render(request, "control_panel/billing_settings.html", {
            "page_title": "Storage & Credit Billing Settings",
            "active": "billing-settings",
            "storage_policy": policy,
            "storage_policy_form": policy_form,
            "billing_cycle_policy": cycle_policy,
            "billing_cycle_form": cycle_form,
            "legacy_cycle_form": legacy_cycle_form,
            "legacy_cycle_policy": legacy_policy,
            "legacy_invoices": CreditCycleInvoice.objects.select_related("company").order_by("-starts_on", "company__name", "category")[:40],
            "can_edit_storage_pricing": can_edit_pricing,
            "can_edit_calendar_billing": can_edit_cycle,
            "time_weighted_storage_enabled": django_settings.STORAGE_MONTHLY_METERED_ENABLED,
        }, status=400 if request.method == "POST" else 200)

    qs = StorageUsage.objects.select_related("company").order_by("-date")
    qs, company_id = _company_filter(request, qs)
    totals = qs.aggregate(bytes=Sum("bytes_used"), files=Sum("file_count"))
    snapshot_count = qs.count()
    covered_companies = qs.values("company_id").distinct().count()

    # Current storage is calculated from REAL owned MediaFile rows rather than
    # summing dated StorageUsage snapshots, which would double-count files.
    company_rows = Company.objects.order_by("name").only("id", "name")
    if company_id:
        company_rows = company_rows.filter(pk=company_id)
    company_page = Paginator(company_rows, 50).get_page(request.GET.get("live_page"))
    company_rows = list(company_page.object_list)
    usage = {
        row["company_id"]: row
        for row in MediaFile.objects.filter(company__in=company_rows)
        .values("company_id").annotate(bytes=Sum("size_bytes"), files=Count("id"))
    }
    period = (
        timezone.localdate().isoformat()
        if policy and policy.billing_period == "daily"
        else timezone.localdate().strftime("%Y-%m")
    )
    references = {
        str(company.pk): f"storage:{company.pk}:{policy.billing_period}:{period}"
        for company in company_rows
    } if policy else {}
    charged_refs = set(
        CreditLedger.objects.filter(reference__in=references.values())
        .values_list("reference", flat=True)
    ) if references else set()
    live_rows = []
    for company in company_rows:
        info = usage.get(company.pk) or {}
        bytes_used = max(0, int(info.get("bytes") or 0))
        used_gb = Decimal(bytes_used) / Decimal(1024 ** 3)
        billable = max(Decimal("0"), used_gb - (policy.included_gb if policy else Decimal("0")))
        rate = policy.credits_per_gb if policy else Decimal("0")
        estimated = (billable * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        live_rows.append({
            "company": company,
            "bytes_used": bytes_used,
            "file_count": info.get("files") or 0,
            "used_gb": used_gb.quantize(Decimal("0.001")),
            "billable_gb": billable.quantize(Decimal("0.001")),
            "estimated_credits": estimated,
            "already_billed": references.get(str(company.pk)) in charged_refs,
        })
    # Upload/delete events are an immutable audit trail, not usage snapshots.
    # Restrict platform browsing to 50 at a time and use the same validated
    # company filter as the live usage/history tables.
    event_qs = StorageEvent.objects.select_related("company", "actor").order_by(
        "-occurred_at", "-created_at", "-id",
    )
    if company_id:
        event_qs = event_qs.filter(company_id=company_id)
    event_page = Paginator(event_qs, 50).get_page(request.GET.get("event_page"))

    return render(request, "control_panel/storage.html", {
        "event_rows": event_page,
        "event_count": event_page.paginator.count,
        "page_title": "Storage", "active": "storage",
        "rows": _paginate(request, qs),
        "total_bytes": totals["bytes"] or 0,
        "total_files": totals["files"] or 0,
        "snapshot_count": snapshot_count,
        "covered_companies": covered_companies,
        "company_id": company_id, "companies": _company_choices(),
        "storage_policy_form": policy_form,
        "storage_policy": policy,
        "billing_cycle_policy": cycle_policy,
        "billing_cycle_form": cycle_form,
        "can_edit_storage_pricing": can_edit_pricing,
        "can_edit_calendar_billing": can_edit_cycle,
        "time_weighted_storage_enabled": django_settings.STORAGE_MONTHLY_METERED_ENABLED,
        "live_storage_rows": live_rows,
        "live_storage_count": company_page.paginator.count,
        "live_storage_page": company_page,
        "storage_period": period,
    })


@panel_admin_required
def security(request):
    all_events = SecurityEvent.objects.all()
    total_events = all_events.count()
    recent_events = all_events.filter(
        created_at__gte=timezone.now() - timedelta(days=7)
    ).count()
    tracked_companies = all_events.exclude(company__isnull=True).values("company_id").distinct().count()
    qs = all_events.select_related("company", "user").order_by("-created_at")
    qs, company_id = _company_filter(request, qs)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(event__icontains=q) | Q(user__email__icontains=q)
            | Q(ip_address__icontains=q)
        )
    return render(request, "control_panel/security.html", {
        "page_title": "Security & Audit Logs", "active": "security",
        "rows": _paginate(request, qs),
        "company_id": company_id, "companies": _company_choices(), "q": q,
        "total_events": total_events, "recent_events": recent_events,
        "tracked_companies": tracked_companies,
    })


@panel_admin_required
def customers(request):
    all_customers = Customer.objects.all()
    total_customers = all_customers.count()
    active_customers = all_customers.filter(status="active").count()
    business_customers = all_customers.filter(customer_type="business").count()
    customer_companies = all_customers.values("company_id").distinct().count()

    qs = all_customers.select_related("company", "branch").order_by("-created_at")
    qs, company_id = _company_filter(request, qs)
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip().lower()
    customer_type = request.GET.get("type", "").strip().lower()
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(phone__icontains=q)
            | Q(email__icontains=q) | Q(company_name__icontains=q)
        )
    if status in {"active", "archived"}:
        qs = qs.filter(status=status)
    else:
        status = ""
    if customer_type in {"individual", "business"}:
        qs = qs.filter(customer_type=customer_type)
    else:
        customer_type = ""

    return render(request, "control_panel/customers.html", {
        "page_title": "All Customers", "active": "customers",
        "rows": _paginate(request, qs),
        "q": q, "status": status, "customer_type": customer_type,
        "company_id": company_id, "companies": _company_choices(),
        "total_customers": total_customers, "active_customers": active_customers,
        "business_customers": business_customers, "customer_companies": customer_companies,
    })


@panel_admin_required
def jobs(request):
    # Overview is based on all saved Job records, not the filtered page.
    all_jobs = Job.objects.all()
    total_jobs = all_jobs.count()
    in_progress_jobs = all_jobs.filter(status=Job.STATUS_IN_PROGRESS).count()
    waiting_parts_jobs = all_jobs.filter(status=Job.STATUS_WAITING_PARTS).count()
    delivered_jobs = all_jobs.filter(status=Job.STATUS_DELIVERED).count()
    priorities = sorted(
        (value for value in all_jobs.order_by().values_list("priority", flat=True).distinct() if value),
        key=str.casefold,
    )

    qs = all_jobs.select_related(
        "company", "branch", "customer", "vehicle", "advisor", "technician"
    ).order_by("-created_at")
    qs, company_id = _company_filter(request, qs)
    status_value = request.GET.get("status", "").strip()
    priority = request.GET.get("priority", "").strip()
    q = request.GET.get("q", "").strip()
    valid_statuses = {value for value, _ in Job.STATUS_CHOICES}
    if status_value in valid_statuses:
        qs = qs.filter(status=status_value)
    else:
        status_value = ""
    if priority and any(option.casefold() == priority.casefold() for option in priorities):
        qs = qs.filter(priority__iexact=priority)
    else:
        priority = ""
    if q:
        qs = qs.filter(
            Q(job_number__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(vehicle__registration__icontains=q)
            | Q(company__name__icontains=q)
            | Q(technician__name__icontains=q)
        )
    return render(request, "control_panel/jobs.html", {
        "page_title": "All Job Cards",
        "active": "jobs",
        "rows": _paginate(request, qs),
        "status_choices": Job.STATUS_CHOICES,
        "status_value": status_value,
        "priority": priority,
        "priorities": priorities,
        "q": q,
        "company_id": company_id,
        "companies": _company_choices(),
        "total_jobs": total_jobs,
        "in_progress_jobs": in_progress_jobs,
        "waiting_parts_jobs": waiting_parts_jobs,
        "delivered_jobs": delivered_jobs,
    })


@panel_admin_required
def stock(request):
    # The same available quantity formula used by StockItem.available_quantity:
    # on hand minus reserved. No stock movement or quantity mutation happens here.
    all_items = StockItem.objects.all()
    total_items = all_items.count()
    low_stock_count = all_items.filter(
        on_hand__lte=F("reserved") + F("minimum_stock")
    ).count()
    out_of_stock_count = all_items.filter(on_hand__lte=F("reserved")).count()
    stock_companies = all_items.values("company_id").distinct().count()
    status_options = sorted(
        (value for value in all_items.order_by().values_list("status", flat=True).distinct() if value),
        key=str.casefold,
    )

    qs = all_items.select_related(
        "company", "branch", "category", "supplier"
    ).order_by("company__name", "name")
    qs, company_id = _company_filter(request, qs)
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    level = request.GET.get("level", "").strip().lower()
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(sku__icontains=q)
            | Q(barcode__icontains=q) | Q(brand__icontains=q)
            | Q(company__name__icontains=q)
        )
    if status:
        if any(value.casefold() == status.casefold() for value in status_options):
            qs = qs.filter(status__iexact=status)
        else:
            status = ""
    if level == "out":
        qs = qs.filter(on_hand__lte=F("reserved"))
    elif level == "low":
        qs = qs.filter(
            on_hand__gt=F("reserved"),
            on_hand__lte=F("reserved") + F("minimum_stock"),
        )
    elif level == "healthy":
        qs = qs.filter(on_hand__gt=F("reserved") + F("minimum_stock"))
    else:
        level = ""

    return render(request, "control_panel/stock.html", {
        "page_title": "Global Stock",
        "active": "stock",
        "rows": _paginate(request, qs),
        "q": q, "status": status, "level": level,
        "status_options": status_options,
        "company_id": company_id,
        "companies": _company_choices(),
        "total_items": total_items,
        "low_stock_count": low_stock_count,
        "out_of_stock_count": out_of_stock_count,
        "stock_companies": stock_companies,
    })


@panel_admin_required
def invoices(request):
    # Counts are across actual platform records, independent of directory filters.
    all_documents = Invoice.objects.all()
    total_documents = all_documents.count()
    invoice_count = all_documents.filter(kind="invoice").count()
    estimate_count = all_documents.filter(kind="estimate").count()
    outstanding_count = all_documents.filter(kind="invoice", balance__gt=0).count()
    status_options = sorted(
        (value for value in all_documents.order_by().values_list("status", flat=True).distinct() if value),
        key=str.casefold,
    )

    qs = all_documents.select_related("company", "branch", "customer").order_by("-date", "-created_at")
    qs, company_id = _company_filter(request, qs)
    q = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "").strip().lower()
    status = request.GET.get("status", "").strip()
    if kind in {"invoice", "estimate"}:
        qs = qs.filter(kind=kind)
    else:
        kind = ""
    if status:
        if any(value.casefold() == status.casefold() for value in status_options):
            qs = qs.filter(status__iexact=status)
        else:
            status = ""
    if q:
        qs = qs.filter(
            Q(number__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(company__name__icontains=q)
        )
    return render(request, "control_panel/invoices.html", {
        "page_title": "Global Billing Documents",
        "active": "invoices",
        "rows": _paginate(request, qs),
        "q": q,
        "kind": kind,
        "status": status,
        "status_options": status_options,
        "company_id": company_id,
        "companies": _company_choices(),
        "total_documents": total_documents,
        "invoice_count": invoice_count,
        "estimate_count": estimate_count,
        "outstanding_count": outstanding_count,
    })


@panel_admin_required
def staff(request):
    # Summary cards represent all workshop staff, not a filtered subset.
    all_staff = Employee.objects.all()
    total_staff = all_staff.count()
    active_staff = all_staff.filter(status__iexact="active").count()
    linked_staff = all_staff.filter(user__isnull=False).count()
    staff_companies = all_staff.values("company_id").distinct().count()
    status_options = sorted(
        (value for value in all_staff.order_by().values_list("status", flat=True).distinct() if value),
        key=str.casefold,
    )

    qs = all_staff.select_related("company", "branch", "team", "shift", "user").order_by("company__name", "name")
    qs, company_id = _company_filter(request, qs)
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    login = request.GET.get("login", "").strip().lower()
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(employee_code__icontains=q)
            | Q(phone__icontains=q)
            | Q(email__icontains=q)
            | Q(designation__icontains=q)
            | Q(company__name__icontains=q)
        )
    if status:
        if any(value.casefold() == status.casefold() for value in status_options):
            qs = qs.filter(status__iexact=status)
        else:
            status = ""
    if login == "linked":
        qs = qs.filter(user__isnull=False)
    elif login == "unlinked":
        qs = qs.filter(user__isnull=True)
    else:
        login = ""

    return render(request, "control_panel/staff.html", {
        "page_title": "All Workshop Staff",
        "active": "staff",
        "rows": _paginate(request, qs),
        "q": q,
        "status": status,
        "login": login,
        "status_options": status_options,
        "company_id": company_id,
        "companies": _company_choices(),
        "total_staff": total_staff,
        "active_staff": active_staff,
        "linked_staff": linked_staff,
        "staff_companies": staff_companies,
        "platform_staff": (
            User.objects.filter(company__isnull=True, is_staff=True, is_superuser=False)
            .select_related("role").order_by("name", "email")
            if panel_permission(request.user, "users", "view") else User.objects.none()
        ),
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


def _can_manage_account(actor, obj):
    if actor.is_superuser:
        return True
    if isinstance(obj, User):
        return not obj.is_superuser and (not obj.role or can_delegate(actor, obj.role.permissions))
    if isinstance(obj, Role):
        return can_delegate(actor, obj.permissions)
    return True


def _send_new_user_invite(request, user):
    """Email the correct password setup/reset link for this existing account.

    Passwords are never sent, and resending to an active account never
    replaces its current password. A setup URL works only for unset accounts.
    """
    if not user or not user.is_active:
        return False
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    is_reset = user.has_usable_password()

    if landing_route(user):
        url = password_action_url(request, user, uid, token, setup=not is_reset)
        purpose = "SaaS Control Panel"
    elif (user.company_id and user.role_id and user.company.is_active
          and user.role.is_active and user.role.company_id == user.company_id
          and not user.is_staff and not user.is_superuser):
        url = password_action_url(request, user, uid, token, setup=not is_reset)
        purpose = "Workshop Company Panel"
    else:
        return False

    instruction = "Reset your password" if is_reset else "Set up your password"
    queue_email(
        f"{instruction} for CubixGear {purpose}",
        f"Your CubixGear {purpose} account is ready. Use the secure "
        f"button below to {instruction.lower()}. If you did not request "
        "this email, you can ignore it.",
        [user.email], action_url=url, action_label=instruction,
    )
    return True

def _save_form(request, form_class, *, instance=None, title, active, cancel_url, form_kwargs=None):
    if not _can_manage_account(request.user, instance):
        return HttpResponseForbidden("Only a superadmin can manage a superadmin account.")
    form = form_class(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        instance=instance,
        **(form_kwargs or {}),
    )
    form.actor = request.user
    if isinstance(form, UserForm) and not request.user.is_superuser:
        for field in ("is_staff", "is_superuser"):
            form.fields[field].disabled = True
    if isinstance(form, UserForm) and instance == request.user:
        for field in ("is_active", "is_staff", "is_superuser"):
            form.fields[field].disabled = True
    if request.method == "POST" and form.is_valid():
        is_new_user = isinstance(form, UserForm) and instance is None
        is_new_staff = isinstance(form, StaffInviteForm) and instance is None
        obj = form.save()
        if is_new_staff:
            if _send_new_user_invite(request, obj.user):
                messages.success(request, "Staff account created. Password setup invitation scheduled by email.")
            else:
                messages.warning(request, "Staff created, but a login invitation could not be scheduled.")
        elif is_new_user and not obj.has_usable_password():
            if _send_new_user_invite(request, obj):
                messages.success(request, "User created. Password setup invitation emailed.")
            else:
                messages.warning(request, "User created, but no login invitation was sent. Assign an appropriate role and verify the account.")
        else:
            messages.success(request, f"{title} saved successfully.")
        return redirect(cancel_url)
    template = (
        "control_panel/company_form.html" if isinstance(form, CompanyForm)
        else "control_panel/user_form.html" if isinstance(form, UserForm)
        else "control_panel/subscription_form.html" if isinstance(form, SubscriptionForm)
        else "control_panel/plan_editor.html" if isinstance(form, PlanBuilderForm)
        else "control_panel/staff_form.html" if isinstance(form, (StaffInviteForm, EmployeeForm))
        and active == "staff"
        else "control_panel/invoice_form.html" if isinstance(form, InvoiceForm) and active == "invoices"
        else "control_panel/stock_form.html" if isinstance(form, StockItemForm) and active == "stock"
        else "control_panel/job_form.html" if isinstance(form, JobForm) and active == "jobs"
        else "control_panel/customer_form.html" if isinstance(form, CustomerForm) and active == "customers"
        else "control_panel/feature_editor.html" if isinstance(form, (PlanFeatureForm, FeatureGroupForm, FeatureBundleForm)) and active == "features"
        else "control_panel/remaining_form.html" if active in {"vehicles", "branches", "roles", "features"}
        else "control_panel/form.html"
    )
    return render(request, template, {
        "page_title": title,
        "active": active,
        "form": form,
        "cancel_url": cancel_url,
        "is_edit": instance is not None,
        "feature_form_kind": (
            "feature" if isinstance(form, PlanFeatureForm)
            else "group" if isinstance(form, FeatureGroupForm)
            else "bundle" if isinstance(form, FeatureBundleForm)
            else None
        ),
        "can_add_platform_staff": (
            active == "staff" and panel_permission(request.user, "users", "create")
        ),
        "role_scope": (
            form.scope or ("platform" if form.instance.pk and form.instance.company_id is None else "workshop")
        ) if isinstance(form, RoleForm) else None,
        "permission_sections": form.permission_sections if isinstance(form, RoleForm) else [],
        "can_grant_wildcard": request.user.is_superuser,
        "wildcard_selected": (
            "*" in (form.data.getlist("permissions") if form.is_bound else (form.instance.permissions or []))
            if isinstance(form, RoleForm) else False
        ),
    })


def _delete_object(request, obj, *, title, active, cancel_url, template_name="control_panel/confirm_delete.html"):
    if not _can_manage_account(request.user, obj):
        return HttpResponseForbidden("You cannot manage an account or role with greater permissions.")
    if request.method == "POST":
        try:
            obj.delete()
            messages.success(request, f"{title} deleted successfully.")
            return redirect(cancel_url)
        except ProtectedError:
            messages.error(request, f"{title} cannot be deleted because other records depend on it.")
            return redirect(cancel_url)
    return render(request, template_name, {
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
    obj = get_object_or_404(
        Company.objects.select_related("owner").annotate(
            user_count=Count("users", distinct=True),
            branch_count=Count("branches", distinct=True),
        ),
        pk=pk,
    )
    return render(request, "control_panel/company_detail.html", {
        "page_title": "Company Details",
        "active": "companies",
        "object": obj,
    })


@panel_admin_required
def company_edit(request, pk):
    obj = get_object_or_404(Company, pk=pk)
    return _save_form(request, CompanyForm, instance=obj, title="Edit Company", active="companies", cancel_url="panel:companies")


@panel_admin_required
def company_delete(request, pk):
    obj = get_object_or_404(Company, pk=pk)
    return _delete_object(
        request, obj, title="Company", active="companies", cancel_url="panel:companies",
        template_name="control_panel/company_delete.html",
    )


@panel_admin_required
def user_create(request):
    return _save_form(request, UserForm, title="Add User", active="users", cancel_url="panel:users")


@panel_admin_required
def user_detail(request, pk):
    obj = get_object_or_404(User.objects.select_related("company", "branch", "role"), pk=pk)
    return render(request, "control_panel/user_detail.html", {
        "page_title": "User Details",
        "active": "users",
        "object": obj,
        "can_manage_target": _can_manage_account(request.user, obj),
    })


@panel_admin_required
def user_edit(request, pk):
    obj = get_object_or_404(User, pk=pk)
    return _save_form(request, UserForm, instance=obj, title="Edit User", active="users", cancel_url="panel:users")


@panel_admin_required
def user_delete(request, pk):
    obj = get_object_or_404(User, pk=pk)
    if not _can_manage_account(request.user, obj):
        return HttpResponseForbidden("Only a superadmin can manage a superadmin account.")
    if obj == request.user:
        messages.error(request, "You cannot delete your own Control Panel account.")
        return redirect("panel:users")
    return _delete_object(
        request, obj, title="User", active="users", cancel_url="panel:users",
        template_name="control_panel/user_delete.html",
    )


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
    # One entry point for workshop staff and companyless SaaS administration.
    account_type = (
        request.POST.get("account_type") if request.method == "POST"
        else request.GET.get("type")
    ) or "workshop"
    if account_type not in {"workshop", "platform"}:
        return HttpResponseBadRequest("Invalid staff account type.")
    if account_type == "workshop":
        return _save_form(
            request, StaffInviteForm, title="Add Staff",
            active="staff", cancel_url="panel:staff",
        )

    # Platform accounts are privileged: staff.create alone is NOT sufficient.
    if not panel_permission(request.user, "users", "create"):
        return HttpResponseForbidden("You do not have permission to create SaaS staff.")
    form = PlatformStaffInviteForm(request.POST if request.method == "POST" else None)
    form.actor = request.user
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
                invitation_requested = _send_new_user_invite(request, user)
        except IntegrityError:
            form.add_error("email", "This email is already registered.")
        else:
            if invitation_requested:
                messages.success(
                    request,
                    "SaaS Admin Staff created. A secure password-setup email was requested. "
                    "Real delivery requires a working SMTP email backend.",
                )
            else:
                messages.warning(
                    request,
                    "Staff account created, but its role has no usable Control Panel "
                    "permission. Fix the role and resend the invitation.",
                )
            return redirect("panel:staff")
    return render(request, "control_panel/platform_staff_form.html", {
        "page_title": "Add SaaS Admin Staff",
        "active": "staff",
        "form": form,
        "cancel_url": "panel:staff",
    })


@panel_admin_required
def staff_detail(request, pk):
    obj = get_object_or_404(Employee.objects.select_related("company", "branch", "user", "team", "shift"), pk=pk)
    return render(request, "control_panel/detail.html", {
        "page_title": "Staff Details", "active": "staff", "object": obj,
        "rows": _display_rows(obj, [
            ("employee_code", "Employee code"), ("name", "Name"), ("company", "Company"),
            ("branch", "Branch"), ("user", "Linked user"), ("phone", "Phone"), ("email", "Email"),
            ("designation", "Designation"), ("role_name", "Role"), ("department_name", "Department"),
            ("team", "Team"), ("shift", "Shift"), ("joining_date", "Joining date"),
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
@require_POST
def staff_invite(request, pk):
    """Invite existing staff, creating a scoped login for legacy staff profiles."""
    employee = get_object_or_404(
        Employee.objects.select_related("company", "branch", "user", "user__role"),
        pk=pk,
    )
    if employee.status != "Active" or not employee.company.is_active:
        messages.warning(request, "Activate the staff member and company before inviting.")
        return _safe_redirect(request, request.POST.get("next"), "panel:staff")

    user = employee.user
    if user:
        if (user.company_id != employee.company_id
                or user.email.lower() != (employee.email or "").strip().lower()
                or user.is_staff or user.is_superuser
                or not _can_manage_account(request.user, user)):
            return HttpResponseForbidden("The linked account cannot be invited as workshop staff.")
    else:
        email = (employee.email or "").strip().lower()
        role = Role.objects.filter(
            company=employee.company, name__iexact=employee.role_name,
            is_active=True, is_system=False,
        ).first()
        if not email or not role:
            messages.warning(request, "Add a valid email and active company staff role first.")
            return _safe_redirect(request, request.POST.get("next"), "panel:staff")
        if User.objects.filter(email__iexact=email).exists():
            messages.warning(request, "This email already belongs to an account. Resolve it first.")
            return _safe_redirect(request, request.POST.get("next"), "panel:staff")
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    email=email, password=None, name=employee.name,
                    company=employee.company, branch=employee.branch,
                    role=role, is_active=True, is_staff=False,
                    is_superuser=False, email_verified=False,
                )
                employee.user = user
                employee.save(update_fields=["user", "updated_at"])
        except IntegrityError:
            messages.warning(request, "Unable to create login: email is already in use.")
            return _safe_redirect(request, request.POST.get("next"), "panel:staff")

    if _send_new_user_invite(request, user):
        messages.success(request, "Staff account email scheduled for the registered address.")
    else:
        messages.warning(request, "No email sent. Verify this account's login permissions.")
    return _safe_redirect(request, request.POST.get("next"), "panel:staff")


@panel_admin_required
def staff_delete(request, pk):
    obj = get_object_or_404(Employee, pk=pk)
    return _delete_object(request, obj, title="Staff", active="staff", cancel_url="panel:staff")


# Supporting records needed to configure companies and create job cards.
PANEL_RESOURCES = {
    'branches': (BranchForm, 'Branches', 'branch', ['name', 'code', 'company__name']),
    'roles': (RoleForm, 'Roles & Permissions', 'role', ['name', 'code', 'company__name']),
    'vehicles': (VehicleForm, 'Vehicles', 'vehicle', ['registration', 'make', 'model', 'customer__name']),
}


@panel_admin_required
def resource_list(request, resource):
    form_class, title, singular, search_fields = PANEL_RESOURCES[resource]
    all_records = form_class._meta.model.objects.all()
    total_records = all_records.count()
    companies_count = all_records.exclude(company__isnull=True).values("company_id").distinct().count()
    if resource in {"branches", "roles"}:
        active_records = all_records.filter(is_active=True).count()
        special_records = (
            all_records.filter(is_head_office=True).count()
            if resource == "branches" else all_records.filter(is_system=True).count()
        )
    else:
        active_records = all_records.filter(status__iexact="active").count()
        special_records = all_records.filter(status__iexact="inactive").count()

    queryset = all_records.select_related("company")
    if resource == "vehicles":
        queryset = queryset.select_related("customer", "branch")
    queryset = queryset.order_by("-created_at", "pk")
    queryset, company_id = _company_filter(request, queryset)
    role_scope_filter = ""
    if resource == "roles":
        role_scope_filter = request.GET.get("scope", "").strip().lower()
        if role_scope_filter == "platform":
            queryset = queryset.filter(company__isnull=True)
        elif role_scope_filter == "workshop":
            queryset = queryset.filter(company__isnull=False)
        else:
            role_scope_filter = ""
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip().lower()
    if query:
        condition = Q()
        for field in search_fields:
            condition |= Q(**{field + "__icontains": query})
        queryset = queryset.filter(condition)
    if status in {"active", "inactive"}:
        if resource in {"branches", "roles"}:
            queryset = queryset.filter(is_active=(status == "active"))
        else:
            queryset = queryset.filter(status__iexact=status)
    else:
        status = ""
    return render(request, "control_panel/resource_list.html", {
        "page_title": title, "active": resource,
        "rows": _paginate(request, queryset),
        "companies": _company_choices(), "company_id": company_id, "q": query,
        "status": status, "total_records": total_records,
        "active_records": active_records, "special_records": special_records,
        "companies_count": companies_count,
        "role_scope_filter": role_scope_filter,
        "platform_roles_count": all_records.filter(company__isnull=True).count() if resource == "roles" else 0,
        "workshop_roles_count": all_records.filter(company__isnull=False).count() if resource == "roles" else 0,
        "create_url": f"panel:{singular}-create", "detail_url": f"panel:{singular}-detail",
        "edit_url": f"panel:{singular}-edit", "delete_url": f"panel:{singular}-delete",
    })


@panel_admin_required
def resource_action(request, resource, action, pk=None, role_scope=None):
    form_class, title, singular, _ = PANEL_RESOURCES[resource]
    if resource == "roles" and action == "create":
        role_scope = role_scope or "workshop"
    obj = get_object_or_404(form_class._meta.model, pk=pk) if pk else None
    cancel_url = f'panel:{resource}'
    if action == 'detail':
        return render(request, 'control_panel/detail.html', {
            'page_title': title, 'active': resource, 'object': obj,
            'rows': _display_rows(obj, [(name, field.label) for name, field in form_class.base_fields.items()]),
            'edit_url': f'panel:{singular}-edit', 'delete_url': f'panel:{singular}-delete',
        })
    if action == 'delete':
        return _delete_object(request, obj, title=title, active=resource, cancel_url=cancel_url)
    editor_title = ('Edit ' if obj else 'Add ') + title
    if resource == "roles" and action == "create":
        editor_title = "Add SaaS Admin Staff Role" if role_scope == "platform" else "Add Workshop Role"
    return _save_form(
        request, form_class, instance=obj, title=editor_title,
        active=resource, cancel_url=cancel_url,
        form_kwargs={"scope": role_scope} if resource == "roles" and action == "create" else None,
    )


# Dedicated SaaS platform tools; never accessible to tenant company admins.
@panel_admin_required
def plan_catalog(request):
    # Plan prices are catalog configuration, not revenue or payment receipts.
    plans = SubscriptionPlan.objects.all()
    plan_total = plans.count()
    plan_active = plans.filter(is_active=True).count()
    plan_public = plans.filter(is_public=True, is_active=True).count()
    subscriptions_assigned = Subscription.objects.filter(
        plan_definition__isnull=False
    ).count()

    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip().lower()
    visibility = request.GET.get("visibility", "").strip().lower()
    if query:
        plans = plans.filter(
            Q(name__icontains=query) | Q(key__icontains=query)
            | Q(description__icontains=query)
        )
    if status == "active":
        plans = plans.filter(is_active=True)
    elif status == "archived":
        plans = plans.filter(is_active=False)
    else:
        status = ""
    if visibility == "public":
        plans = plans.filter(is_public=True)
    elif visibility == "private":
        plans = plans.filter(is_public=False)
    else:
        visibility = ""

    plans = plans.annotate(
        enabled_feature_count=Count(
            "feature_access", filter=Q(feature_access__enabled=True), distinct=True
        )
    ).prefetch_related("feature_access__feature")

    return render(request, "control_panel/plan_catalog.html", {
        "page_title": "Plan Builder", "active": "plans",
        "plans": plans,
        "q": query, "status": status, "visibility": visibility,
        "plan_total": plan_total, "plan_active": plan_active,
        "plan_public": plan_public,
        "subscriptions_assigned": subscriptions_assigned,
    })


@panel_admin_required
def plan_create(request):
    return _save_form(request, PlanBuilderForm, title="Create Plan",
                      active="plans", cancel_url="panel:plans")


@panel_admin_required
def plan_edit(request, pk):
    obj = get_object_or_404(SubscriptionPlan, pk=pk)
    return _save_form(request, PlanBuilderForm, instance=obj, title="Edit Plan",
                      active="plans", cancel_url="panel:plans")

@panel_admin_required
@require_http_methods(["GET", "POST"])
def plan_delete(request, pk):
    """Delete only unused plan definitions; never orphan or cascade billing history."""
    plan = get_object_or_404(SubscriptionPlan, pk=pk)
    subscription_count = Subscription.objects.filter(plan_definition=plan).count()
    request_count = PlanChangeRequest.objects.filter(requested_plan=plan).count()
    is_used = bool(subscription_count or request_count)
    if request.method == "POST":
        if is_used:
            messages.error(
                request,
                "This plan is used by subscriptions or plan-change requests. "
                "Archive it via Edit Plan instead of deleting billing history.",
            )
            return redirect("panel:plans")
        try:
            with transaction.atomic():
                # Protected foreign keys are checked again by the database/collector
                # to cover references created since the confirmation page loaded.
                plan.delete()
                record_panel_event(request, "PLAN_DELETED", {
                    "plan_id": pk, "plan_key": plan.key, "plan_name": plan.name,
                })
        except (ProtectedError, IntegrityError):
            messages.error(
                request, "Cannot delete this plan while dependent records exist. "
                "Archive it from Edit Plan instead.",
            )
            return redirect("panel:plans")
        messages.success(request, f"Plan {plan.name} deleted.")
        return redirect("panel:plans")
    return render(request, "control_panel/plan_delete.html", {
        "page_title": "Delete Plan", "active": "plans", "object": plan,
        "subscription_count": subscription_count, "request_count": request_count,
        "is_used": is_used,
    })




@panel_admin_required
def feature_catalog(request):
    """Keep historical bookmarks working after retiring the labels page."""
    return redirect("panel:feature-pricing")


@panel_admin_required
def feature_bundle_create(request):
    return _save_form(request, FeatureBundleForm, title="Create Paid Feature Label",
                      active="features", cancel_url="panel:features")


@panel_admin_required
def feature_bundle_edit(request, pk):
    obj = get_object_or_404(FeatureBundle, pk=pk)
    if hasattr(obj, "module_price"):
        return redirect(reverse("panel:feature-pricing") + "#module-" + obj.module_price.feature.key)
    return _save_form(request, FeatureBundleForm, instance=obj, title="Edit Paid Feature Label",
                      active="features", cancel_url="panel:features")


@panel_admin_required
@require_POST
def feature_sync(request):
    """Backwards-compatible administrator-only sync, intentionally hidden from /features/."""
    if not panel_permission(request.user, "features", "edit"):
        return HttpResponseForbidden("Feature catalog edit permission required.")
    with transaction.atomic():
        created = sync_existing_modules()
    messages.success(request, f"Discovered {len(created)} new backend modules. No prices or access grants were changed.")
    return redirect("panel:features")


@panel_admin_required
def feature_create(request):
    return _save_form(request, PlanFeatureForm, title="Add Feature",
                      active="features", cancel_url="panel:features")


@panel_admin_required
def feature_group_create(request):
    return _save_form(request, FeatureGroupForm, title="Add Feature Label / Group",
                      active="features", cancel_url="panel:features")


@panel_admin_required
def plan_requests(request):
    return render(request, "control_panel/plan_requests.html", {
        "page_title": "Subscription Enquiries", "active": "requests",
        "changes": PlanChangeRequest.objects.select_related("company", "requested_plan", "requested_by")[:100],
        "custom": CustomPlanRequest.objects.select_related("company", "requested_by")[:100],
    })


@panel_admin_required
def feature_edit(request, pk):
    obj = get_object_or_404(PlanFeature, pk=pk)
    return _save_form(request, PlanFeatureForm, instance=obj, title="Edit Feature",
                      active="features", cancel_url="panel:features")


@panel_admin_required
def feature_group_edit(request, pk):
    obj = get_object_or_404(FeatureGroup, pk=pk)
    return _save_form(request, FeatureGroupForm, instance=obj, title="Edit Feature Label",
                      active="features", cancel_url="panel:features")
