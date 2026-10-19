"""Platform credit package and wallet controls; legacy feature/storage price forms retired."""
import uuid
from django.contrib import messages
from django.db.models import Exists, OuterRef
from django.db.models.deletion import ProtectedError
from django.db import transaction, IntegrityError
from django.http import HttpResponseForbidden, HttpResponseGone
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from apps.accounts.permissions import panel_permission
from apps.control_panel.views import panel_admin_required
from apps.control_panel.audit import record_panel_event
from apps.companies.models import Company
from .forms import CreditPackageForm, CreditAdjustmentForm, MonthlyBillingPolicyForm
from .models import CreditPackage, CreditWallet, CreditLedger, CreditPurchase, MonthlyBillingPolicy
from .service import adjust_company_balance


@panel_admin_required
@require_http_methods(["GET", "POST"])
def credits_control(request):
    billing_config = MonthlyBillingPolicy.objects.filter(pk=1).first()
    billing_form = MonthlyBillingPolicyForm(
        request.POST if request.method == 'POST' and request.POST.get('action') == 'monthly_policy' else None,
        instance=billing_config,
    )
    if request.method == "POST":
        if not panel_permission(request.user, "credits", "edit"):
            return HttpResponseForbidden("Credit management permission is required.")
        action = request.POST.get("action")
        if action == 'monthly_policy':
            if billing_form.is_valid():
                saved = billing_form.save(commit=False)
                saved.pk = 1
                saved.save()
                messages.success(request, 'Monthly collection policy saved. Issued deadlines are preserved.')
                return redirect('panel:credits')
        elif action == "delete_package":
            if not panel_permission(request.user, "credits", "delete"):
                return HttpResponseForbidden("Credit package delete permission is required.")
            if request.POST.get("confirm_delete") != "yes":
                messages.error(request, "Confirm deletion before removing the package.")
            else:
                pk = request.POST.get("package_id")
                package = get_object_or_404(CreditPackage, pk=pk)
                # Historical purchases permanently reference the package.
                # Never delete packages used by any verified or pending order.
                if CreditPurchase.objects.filter(package_id=package.pk).exists():
                    messages.error(request, "This package has purchase history. Edit it and uncheck Published instead.")
                else:
                    package_id, package_name = package.pk, package.name
                    try:
                        with transaction.atomic():
                            package.delete()
                            record_panel_event(request, "CREDIT_PACKAGE_DELETED", {
                                "package_id": package_id,
                                "package_name": package_name,
                            })
                    except (ProtectedError, IntegrityError):
                        messages.error(request, "This package is referenced by other records and cannot be deleted. Hide it instead.")
                    else:
                        messages.success(request, f"Credit package '{package_name}' deleted.")
                        return redirect("panel:credits")
        elif action == "package":
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

    # Preserve failed package submissions, including edits, without losing field values.
    package_draft = request.POST if request.method == "POST" and request.POST.get("action") == "package" else {}
    can_manage_packages = panel_permission(request.user, "credits", "edit")
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
    packages = CreditPackage.objects.annotate(
        has_purchases=Exists(
            CreditPurchase.objects.filter(package_id=OuterRef("pk"))
        )
    ).order_by("sort_order", "id")
    purchases = CreditPurchase.objects.select_related("company", "package").order_by("-created_at")[:25]
    return render(request, "control_panel/credits.html", {
        "monthly_billing_form": billing_form,
        "page_title": "Credit Wallet",
        "active": "credits",
        "packages": packages,
        "can_manage_packages": can_manage_packages,
        "can_delete_packages": panel_permission(request.user, "credits", "delete"),
        "package_draft": package_draft,
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
