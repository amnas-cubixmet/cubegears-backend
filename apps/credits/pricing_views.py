from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from apps.accounts.permissions import panel_permission
from apps.control_panel.views import panel_admin_required
from apps.saas.feature_discovery import module_inventory
from .models import ModulePriceLabel
from .module_pricing import ModulePriceForm, save_module_price


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
                save_module_price(submitted.cleaned_data, request.user)
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
            'module':module['key'], 'per_user_credits':bundle.per_user_credits if bundle else 0,
            'billing_type':bundle.billing_type if bundle else 'monthly', 'published':bundle.is_active if bundle else False,
        }, auto_id='id_' + module['key'] + '_%s')
        rows.append({'module':module, 'form':form, 'mapping':mapping, 'bundle':bundle})
    return render(request, 'control_panel/module_pricing.html', {
        'page_title':'Module Pricing', 'active':'features', 'rows':rows, 'can_edit':can_edit,
        'submission_errors': submitted.errors if submitted is not None and request.POST.get('module') not in [r['module']['key'] for r in rows] else None,
    }, status=400 if submitted is not None and submitted.errors else 200)
