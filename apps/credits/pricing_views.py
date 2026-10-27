from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from apps.accounts.permissions import panel_permission
from apps.control_panel.views import panel_admin_required
from apps.saas.feature_discovery import module_inventory
from .models import ModulePriceLabel, FeatureBundle, CompanyPostpaidFeature
from .module_pricing import ModulePriceForm, save_module_price
from apps.control_panel.audit import record_panel_event


@panel_admin_required
@require_http_methods(['GET', 'POST'])
def module_pricing(request):
    can_edit = panel_permission(request.user, 'features', 'edit')
    submitted = None
    if request.method == 'POST':
        if not can_edit:
            return HttpResponseForbidden('Feature price edit permission required.')
        submitted = ModulePriceForm(request.POST)
        if submitted.is_valid():
            try:
                with transaction.atomic():
                    save_module_price(submitted.cleaned_data, request.user)
                    record_panel_event(request, 'FEATURE_PRICE_UPDATED', {
                        'module': submitted.cleaned_data['module'],
                        'credits': str(submitted.cleaned_data['per_user_credits']),
                        'billing_type': submitted.cleaned_data['billing_type'],
                        'published': bool(submitted.cleaned_data['published']),
                    })
            except ValueError as exc:
                submitted.add_error(None, str(exc))
            else:
                messages.success(request, 'Module price saved. Company features remain OFF until the owner activates them.')
                return redirect('panel:feature-pricing')
    configured = {m.feature.key: m for m in ModulePriceLabel.objects.select_related('feature', 'bundle', 'updated_by')}
    # Purchased prepaid pricing may be changed at any time: existing entitlement
    # periods are preserved through automatically versioned FeatureBundle records.
    # Only genuinely metered postpaid activity keeps its billing model locked.
    mapped_feature_ids = [mapping.feature_id for mapping in configured.values()]
    metered_feature_ids = set(
        CompanyPostpaidFeature.objects.filter(feature_id__in=mapped_feature_ids)
        .values_list('feature_id', flat=True)
    )
    purchased_bundle_ids = set(
        FeatureBundle.objects.filter(
            pk__in=[mapping.bundle_id for mapping in configured.values()],
            company_access__isnull=False,
        ).values_list('pk', flat=True)
    )
    rows = []
    for module in module_inventory():
        if not module['billable']:
            continue
        mapping = configured.get(module['key'])
        bundle = mapping.bundle if mapping else None
        has_purchases = bool(mapping and mapping.bundle_id in purchased_bundle_ids)
        has_metered_access = bool(mapping and mapping.feature_id in metered_feature_ids)
        billing_model_locked = has_metered_access
        # Rate unit is editable at any time: ongoing metering is first
        # checkpointed, preserving historical usage and then repriced.
        billing_type_locked = False
        form = submitted if submitted is not None and request.POST.get('module') == module['key'] else ModulePriceForm(initial={
            'module':module['key'], 'monthly_postpaid':bool(mapping and mapping.monthly_postpaid), 'per_user_credits':bundle.per_user_credits if bundle else 5,
            'billing_type':bundle.billing_type if bundle else 'monthly', 'published':bundle.is_active if bundle else True,
        }, auto_id='id_' + module['key'] + '_%s')
        rows.append({
            'module': module, 'form': form, 'mapping': mapping, 'bundle': bundle,
            'billing_type_locked': billing_type_locked,
            'billing_model_locked': billing_model_locked,
            'has_purchases': has_purchases,
            'has_metered_access': has_metered_access,
        })
    return render(request, 'control_panel/module_pricing.html', {
        'page_title':'Feature Pricing Settings', 'active':'features', 'pricing_page':True, 'rows':rows, 'can_edit':can_edit,
        'module_count': len(rows), 'min_user_credits': 5,
        'submission_errors': submitted.errors if submitted is not None and request.POST.get('module') not in [r['module']['key'] for r in rows] else None,
    })
    # Normal Django form handling: show per-field validation errors on this page.
    # An invalid edit never writes pricing or entitlement data.
