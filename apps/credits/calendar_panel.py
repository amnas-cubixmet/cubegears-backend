"""Platform configuration for calendar-month usage billing (not the old 5th-to-5th engine)."""
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.permissions import panel_permission
from apps.control_panel.views import panel_admin_required
from .billing_cycle_forms import BillingCyclePolicyForm
from .models import BillingCyclePolicy, BillingCycleStatement


@panel_admin_required
@require_http_methods(["GET", "POST"])
def calendar_policy_control(request):
    can_edit = panel_permission(request.user, "credits", "edit")
    if request.method == "POST" and not can_edit:
        return HttpResponseForbidden("Platform credit policy edit permission required.")

    current = BillingCyclePolicy.objects.filter(singleton=1).first()
    form = BillingCyclePolicyForm(
        request.POST if request.method == "POST" else None, instance=current,
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
        messages.success(
            request,
            "Calendar month billing settings saved. New usage settles only on the due day.",
        )
        return redirect("panel:calendar-credit-billing")

    invoices = BillingCycleStatement.objects.select_related("company").order_by(
        "-month", "company__name",
    )[:75]
    return render(request, "control_panel/calendar_billing.html", {
        "page_title": "Calendar Month Credit Billing",
        "active": "credits",
        "can_edit": can_edit,
        "form": form,
        "policy": current,
        "invoices": invoices,
    }, status=400 if request.method == "POST" and form.errors else 200)
