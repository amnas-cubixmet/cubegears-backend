"""Platform-only configuration; untrusted tenants can never choose due days/rates."""
from django import forms
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.accounts.permissions import panel_permission
from apps.companies.models import Company
from apps.control_panel.views import panel_admin_required
from apps.saas.storage_ledger import ensure_baseline
from .models import CreditCycleInvoice, CreditCyclePolicy


class CreditCyclePolicyForm(forms.ModelForm):
    class Meta:
        model = CreditCyclePolicy
        fields = ["day_of_month", "enabled", "block_overdue"]
        labels = {
            "day_of_month": "Cycle ending / wallet deduction date (day 1–28)",
            "enabled": "Enable deferred monthly billing for new usage",
            "block_overdue": "Restrict workshop access after an unpaid due invoice",
        }
        widgets = {"day_of_month": forms.NumberInput(attrs={"min": "1", "max": "28", "step": "1"})}

    def clean_day_of_month(self):
        day = self.cleaned_data["day_of_month"]
        if not 1 <= day <= 28:
            raise forms.ValidationError("Choose a day between 1 and 28.")
        return day

    def clean(self):
        values = super().clean()
        if values.get("enabled"):
            from .calendar_billing import active_policy
            if active_policy():
                raise forms.ValidationError(
                    "Calendar-month billing is active. Disable it before enabling the old 5th-to-5th cycle."
                )
        if not values.get("enabled"):
            if self.instance.pk and self.instance.enabled and CreditCycleInvoice.objects.filter(
                status__in=("pending", "insufficient"), credits_due__gt=0,
            ).exists():
                raise forms.ValidationError("Settle outstanding cycle invoices before disabling this policy.")
            return values
        if self.instance.pk and self.instance.enabled and self.instance.day_of_month != values.get("day_of_month"):
            raise forms.ValidationError("Cycle date cannot change while billing is active. Settle bills, disable and re-enable with a new date.")
        from .models import BillingCyclePolicy, MonthlyBillingPolicy
        if BillingCyclePolicy.objects.filter(enabled=True).exists() or MonthlyBillingPolicy.objects.filter(enabled=True).exists():
            raise forms.ValidationError(
                "Disable and reconcile the calendar-month and older monthly collection policies before enabling this cycle."
            )
        # Fail closed instead of allowing two independent metering/debit engines
        # to charge the same tenant storage or features for overlapping dates.
        if any((
            settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED,
            settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED,
            settings.STORAGE_MONTHLY_METERED_ENABLED,
        )):
            raise forms.ValidationError("Disable older postpaid feature/storage engines before enabling cycle billing.")
        if not (settings.CREDIT_BILLING_CELERY_ENABLED and
                settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED and
                settings.CREDIT_LABEL_ONLY_ACCESS_ENABLED):
            raise forms.ValidationError(
                "Cycle billing needs Redis/Celery billing, paid feature enforcement and label-only access enabled."
            )
        return values


def save_credit_cycle_policy(form):
    """Apply the validated policy with activation baselines and no debit."""
    with transaction.atomic():
        saved = form.save(commit=False)
        if saved.enabled and (not form.instance.pk or not form.instance.enabled):
            saved.activated_at = timezone.now()
            for company in Company.objects.filter(is_active=True).iterator():
                ensure_baseline(company)
        saved.save()
    return saved


@panel_admin_required
@require_http_methods(["GET", "POST"])
def cycle_policy_control(request):
    can_edit = panel_permission(request.user, "credits", "edit")
    if request.method == "POST" and not can_edit:
        return HttpResponseForbidden("Platform credit policy edit permission required.")
    existing = CreditCyclePolicy.objects.order_by("pk").first()
    form = CreditCyclePolicyForm(request.POST if request.method == "POST" else None, instance=existing)
    if request.method == "POST" and form.is_valid():
        save_credit_cycle_policy(form)
        messages.success(request, "Cycle policy saved. Wallet charges occur only after a closed cycle via the worker.")
        from django.urls import reverse
        from apps.control_panel.permissions import route_allowed
        if route_allowed(request.user, "billing-settings"):
            return redirect(reverse("panel:billing-settings") + "#legacy-credit-cycle")
        return redirect("panel:credit-cycle")
    if request.method == "GET":
        from django.urls import reverse
        from apps.control_panel.permissions import route_allowed
        if route_allowed(request.user, "billing-settings"):
            return redirect(reverse("panel:billing-settings") + "#legacy-credit-cycle")
    invoices = CreditCycleInvoice.objects.select_related("company").order_by(
        "-starts_on", "company__name", "category",
    )[:60]
    return render(request, "control_panel/credit_cycle.html", {
        "page_title": "Credit Billing Cycle",
        "active": "credits",
        "cycle_form": form,
        "can_edit": can_edit,
        "policy": existing,
        "invoices": invoices,
    }, status=400 if request.method == "POST" and form.errors else 200)
