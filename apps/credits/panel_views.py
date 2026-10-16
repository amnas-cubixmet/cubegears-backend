"""Platform credit package and wallet controls; legacy feature/storage price forms retired."""
import uuid
from django.contrib import messages
from django.http import HttpResponseForbidden, HttpResponseGone
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from apps.accounts.permissions import panel_permission
from apps.control_panel.views import panel_admin_required
from apps.companies.models import Company
from .forms import CreditPackageForm, CreditAdjustmentForm
from .models import CreditPackage, CreditWallet, CreditLedger, CreditPurchase
from .service import adjust_company_balance


@panel_admin_required
@require_http_methods(["GET", "POST"])
def credits_control(request):
    if request.method == "POST":
        if not panel_permission(request.user, "credits", "edit"):
            return HttpResponseForbidden("Credit management permission is required.")
        action = request.POST.get("action")
        if action == "package":
            pk = request.POST.get("package_id") or None
            instance = get_object_or_404(CreditPackage, pk=pk) if pk else None
            form = CreditPackageForm(request.POST, instance=instance)
            if form.is_valid():
                form.save()
                messages.success(request, "Credit package saved.")
                return redirect("panel:credits")
            # Form errors are user-facing plain text, not raw HTML <ul> markup.
            errors = " ".join(
                f"{form.fields[name].label if name in form.fields else name}: {', '.join(messages_)}"
                for name, messages_ in form.errors.items()
            )
            messages.error(request, "Invalid package. " + errors)
        elif action in {"price", "storage"}:
            # Old HTML forms are permanently retired. Reject even manually
            # crafted POSTs so they cannot mutate legacy billing rules.
            return HttpResponseGone(
                "Legacy pricing controls have been removed from Credit Wallet Control."
            )
        elif action == "adjust":
            form = CreditAdjustmentForm(request.POST)
            if form.is_valid():
                data = form.cleaned_data
                try:
                    event = adjust_company_balance(
                        data["company"], data["delta"],
                        reason=data["reason"].strip(), actor=request.user,
                        reference="manual:" + str(data["request_id"]),
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, f"Credit ledger entry {event.pk} recorded.")
                    return redirect("panel:credits")
            else:
                messages.error(request, "Invalid adjustment. " + str(form.errors))
        else:
            messages.error(request, "Unknown form action.")

    company_id = request.GET.get("company", "").strip()
    records = CreditLedger.objects.select_related("company", "actor").order_by("-created_at")
    if company_id:
        try:
            uuid.UUID(company_id)
        except ValueError:
            company_id = ""
        else:
            records = records.filter(company_id=company_id)
    wallets = CreditWallet.objects.select_related("company").order_by("company__name")
    packages = CreditPackage.objects.all().order_by("sort_order", "id")
    purchases = CreditPurchase.objects.select_related("company", "package").order_by("-created_at")[:25]
    return render(request, "control_panel/credits.html", {
        "page_title": "Credit Wallet Control",
        "active": "credits",
        "packages": packages,
        "wallets": wallets[:100],
        "companies": Company.objects.filter(is_active=True).order_by("name"),
        "logs": records[:100],
        "purchases": purchases,
        "filter_company": company_id,
        "adjustment_request_id": str(uuid.uuid4()),
        "package_total": CreditPackage.objects.count(),
        "published_packages": CreditPackage.objects.filter(is_active=True).count(),
        "wallet_total": CreditWallet.objects.count(),
    })
