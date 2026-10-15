"""Platform staff credit controls: prices, packages, company grants and logs."""
import uuid
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from apps.accounts.permissions import panel_permission
from apps.control_panel.views import panel_admin_required
from apps.companies.models import Company
from apps.saas.models import PlanFeature
from .forms import CreditPackageForm, FeatureCreditPriceForm, CreditAdjustmentForm, StorageCreditPolicyForm
from .models import CreditPackage, FeatureCreditPrice, CreditWallet, CreditLedger, CreditPurchase, StorageCreditPolicy
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
            messages.error(request, "Invalid package. " + str(form.errors))
        elif action == "price":
            key = request.POST.get("feature")
            feature = get_object_or_404(PlanFeature, pk=key)
            row = FeatureCreditPrice.objects.filter(feature=feature).first()
            form = FeatureCreditPriceForm(request.POST, instance=row)
            if form.is_valid() and form.cleaned_data["feature"].id == feature.id:
                form.save()
                messages.success(request, "Feature credit price saved.")
                return redirect("panel:credits")
            messages.error(request, "Invalid feature price. " + str(form.errors))
        elif action == "storage":
            policy = StorageCreditPolicy.objects.order_by("pk").first()
            form = StorageCreditPolicyForm(request.POST, instance=policy)
            if form.is_valid():
                form.save()
                messages.success(request, "Storage policy saved. Charges begin only when the scheduled billing command runs.")
                return redirect("panel:credits")
            messages.error(request, "Invalid storage credit policy. " + str(form.errors))
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
    prices = FeatureCreditPrice.objects.select_related("feature").order_by("feature__label")
    purchases = CreditPurchase.objects.select_related("company", "package").order_by("-created_at")[:25]
    return render(request, "control_panel/credits.html", {
        "page_title": "Credit Wallet Control",
        "active": "credits",
        "packages": packages,
        "storage_policy": StorageCreditPolicy.objects.order_by("pk").first(),
        "prices": prices,
        "features": PlanFeature.objects.filter(is_active=True).order_by("label"),
        "wallets": wallets[:100],
        "companies": Company.objects.filter(is_active=True).order_by("name"),
        "logs": records[:100],
        "purchases": purchases,
        "filter_company": company_id,
        "adjustment_request_id": str(uuid.uuid4()),
        "package_total": CreditPackage.objects.count(),
        "published_packages": CreditPackage.objects.filter(is_active=True).count(),
        "configured_features": FeatureCreditPrice.objects.count(),
        "wallet_total": CreditWallet.objects.count(),
    })
