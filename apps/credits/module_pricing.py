"""Platform-only module prices backed by the existing per-user label purchases."""
from decimal import Decimal

MIN_MODULE_USER_CREDITS = Decimal('5.00')
from uuid import uuid4
from django import forms
from django.db import transaction
from apps.saas.feature_discovery import module_inventory
from apps.saas.models import FeatureGroup, PlanFeature
from .models import FeatureBundle, ModulePriceLabel, FeatureCreditPrice, CompanyPostpaidFeature


class ModulePriceForm(forms.Form):
    module = forms.ChoiceField(widget=forms.HiddenInput)
    per_user_credits = forms.DecimalField(max_digits=12, decimal_places=2, min_value=MIN_MODULE_USER_CREDITS, max_value=Decimal('1000000'), label='Credits per active login user', help_text='Minimum 5 credits per active user per billing period.')
    billing_type = forms.ChoiceField(choices=[p for p in FeatureBundle.PERIODS if p[0] != 'per_use'])
    monthly_postpaid = forms.BooleanField(required=False, label='Use now, pay next month (monthly postpaid)')
    published = forms.BooleanField(required=False, label='Published for activation')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['module'].choices = [(m['key'], m['label']) for m in module_inventory() if m['billable']]

    def clean(self):
        data = super().clean()
        existing = ModulePriceLabel.objects.select_related('bundle').filter(feature__key=data.get('module')).first()
        if (existing and existing.bundle.company_access.exists() and data.get('billing_type') != existing.bundle.billing_type):
            self.add_error('billing_type', 'A purchased module must keep its billing period. Create a separate feature label for a new period.')
        if data.get('monthly_postpaid') and data.get('billing_type') != 'monthly':
            self.add_error('billing_type', 'Postpaid usage requires Monthly billing.')
        if existing and data.get('monthly_postpaid', False) != existing.monthly_postpaid:
            if existing.bundle.company_access.exists() or CompanyPostpaidFeature.objects.filter(feature=existing.feature).exists():
                self.add_error('monthly_postpaid', 'A module with purchased or metered access must retain its billing model.')
        return data


@transaction.atomic
def save_module_price(data, actor):
    """No wallet or entitlement writes; only authorized platform views call this."""
    from apps.accounts.permissions import panel_permission
    if not panel_permission(actor, 'features', 'edit'):
        raise PermissionError('Feature price edit permission required.')
    if not MIN_MODULE_USER_CREDITS <= Decimal(data['per_user_credits']) <= Decimal('1000000'):
        raise ValueError('Module pricing must be at least 5 credits per active user.')
    module = next(m for m in module_inventory() if m['key'] == data['module'] and m['billable'])
    group, _ = FeatureGroup.objects.get_or_create(key='module-prices', defaults={'label':'Module Pricing'})
    feature, _ = PlanFeature.objects.get_or_create(key=module['key'], defaults={'label':module['label'], 'group':group})
    feature = PlanFeature.objects.select_for_update().get(pk=feature.pk)
    if data['published'] and not feature.is_active:
        feature.is_active = True
        feature.save(update_fields=['is_active'])
    mapping = ModulePriceLabel.objects.select_related('bundle').filter(feature=feature).first()
    if data.get('monthly_postpaid') and data['billing_type'] != 'monthly':
        raise ValueError('Postpaid usage requires Monthly billing.')
    if mapping:
        if data.get('monthly_postpaid', False) != mapping.monthly_postpaid and (mapping.bundle.company_access.exists() or CompanyPostpaidFeature.objects.filter(feature=feature).exists()):
            raise ValueError('A purchased or metered module must keep its billing model.')
        bundle = FeatureBundle.objects.select_for_update().get(pk=mapping.bundle_id)
        if bundle.company_access.exists() and data['billing_type'] != bundle.billing_type:
            raise ValueError('Cannot change the billing period of a purchased module.')
    else:
        bundle = FeatureBundle(key='module-' + uuid4().hex, name=feature.label, price_credits=Decimal('0'))
    bundle.name = feature.label
    bundle.per_user_credits = data['per_user_credits']
    bundle.billing_type = data['billing_type']
    bundle.is_active = data['published']
    bundle.price_credits = Decimal('0')
    bundle.activation_credits = Decimal('0')
    bundle.save()
    bundle.features.set([feature])
    mapping, _ = ModulePriceLabel.objects.get_or_create(feature=feature, defaults={'bundle':bundle})
    mapping.monthly_postpaid = data.get('monthly_postpaid', False)
    if mapping.monthly_postpaid:
        FeatureCreditPrice.objects.update_or_create(feature=feature, defaults={
            'base_credits': Decimal('0'), 'activation_credits': Decimal('0'),
            'per_staff_credits': Decimal('0'), 'per_user_credits': data['per_user_credits'],
            'billing_period': 'monthly', 'is_available': data['published'],
        })
    mapping.updated_by = actor
    mapping.save(update_fields=['monthly_postpaid', 'updated_by', 'updated_at'])
    return bundle
