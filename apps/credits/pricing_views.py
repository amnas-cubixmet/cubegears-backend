from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from apps.accounts.permissions import panel_permission
from apps.control_panel.views import panel_admin_required
from apps.saas.feature_discovery import module_inventory
from apps.saas.models import PlanFeature, FeatureGroup
from .models import ModulePriceLabel, FeatureBundle
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
    rows = []
    for module in module_inventory():
        if not module['billable']:
            continue
        mapping = configured.get(module['key'])
        bundle = mapping.bundle if mapping else None
        form = submitted if submitted is not None and request.POST.get('module') == module['key'] else ModulePriceForm(initial={
            'module':module['key'], 'monthly_postpaid':bool(mapping and mapping.monthly_postpaid), 'per_user_credits':bundle.per_user_credits if bundle else 0,
            'billing_type':bundle.billing_type if bundle else 'monthly', 'published':bundle.is_active if bundle else False,
        }, auto_id='id_' + module['key'] + '_%s')
        rows.append({'module':module, 'form':form, 'mapping':mapping, 'bundle':bundle})
    priced_count = sum(1 for row in rows if row['mapping'])
    return render(request, 'control_panel/module_pricing.html', {
        'page_title':'Module Pricing', 'active':'features', 'rows':rows, 'can_edit':can_edit,
        'module_count': len(rows), 'priced_count': priced_count,
        'unpriced_count': len(rows) - priced_count, 'min_user_credits': 5,
        'catalog_features': PlanFeature.objects.select_related('group').order_by('group__label', 'sort_order', 'label'),
        'catalog_groups': FeatureGroup.objects.select_related('parent').order_by('sort_order', 'label'),
        'catalog_bundles': FeatureBundle.objects.order_by('name'),
        'submission_errors': submitted.errors if submitted is not None and request.POST.get('module') not in [r['module']['key'] for r in rows] else None,
    }, status=400 if submitted is not None and submitted.errors else 200)
