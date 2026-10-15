from decimal import Decimal
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, PlanFeature
from apps.saas.feature_discovery import module_inventory
from .models import ModulePriceLabel, FeatureBundle, CreditWallet, CreditLedger, CompanyBundleAccess


class ModulePricingPanelTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='pricing-platform@example.test', password='SecurePricingTest123!', name='Platform')
        self.client.force_login(self.admin)
        self.url = reverse('panel:feature-pricing')

    def payload(self, **changes):
        return {'module':'jobs', 'per_user_credits':'12.50', 'billing_type':'monthly', 'published':'on', **changes}

    def platform_staff(self, edit):
        role = Role.objects.create(name='Pricing Staff', code='PRICING', permissions=['panel.features.view'] + (['panel.features.edit'] if edit else []))
        return User.objects.create_user(email='delegated-pricing@example.test', name='SaaS staff', is_staff=True, role=role)

    def test_lists_every_billable_module_without_writing_or_inventing_prices(self):
        result = self.client.get(self.url)
        self.assertEqual(result.status_code, 200)
        for module in module_inventory():
            if module['billable']:
                self.assertContains(result, 'id="module-' + module['key'] + '"')
        self.assertContains(result, 'id="module-dashboard"')
        self.assertFalse(PlanFeature.objects.exists())
        self.assertFalse(FeatureBundle.objects.exists())
        self.assertFalse(ModulePriceLabel.objects.exists())

    def test_admin_can_save_update_and_unpublish_without_charging_companies(self):
        self.assertRedirects(self.client.post(self.url, self.payload()), self.url)
        row = ModulePriceLabel.objects.select_related('bundle').get(feature__key='jobs')
        self.assertEqual(row.bundle.per_user_credits, Decimal('12.50'))
        self.assertEqual(row.bundle.price_credits, Decimal('0'))
        self.assertEqual(row.bundle.features.count(), 1)
        self.assertEqual(row.updated_by, self.admin)
        self.assertFalse(CompanyBundleAccess.objects.exists())
        self.assertFalse(CreditLedger.objects.exists())
        self.assertRedirects(self.client.post(self.url, self.payload(per_user_credits='15', published='')), self.url)
        row.bundle.refresh_from_db()
        self.assertEqual(row.bundle.per_user_credits, Decimal('15'))
        self.assertFalse(row.bundle.is_active)
        self.assertEqual(ModulePriceLabel.objects.count(), 1)

    def test_delegated_saas_admin_staff_can_edit_prices(self):
        self.client.force_login(self.platform_staff(edit=True))
        self.assertRedirects(self.client.post(self.url, self.payload()), self.url)
        self.assertEqual(ModulePriceLabel.objects.count(), 1)

    def test_view_only_staff_cannot_write_prices(self):
        self.client.force_login(self.platform_staff(edit=False))
        self.assertContains(self.client.get(self.url), 'You have view access')
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 403)
        self.assertFalse(ModulePriceLabel.objects.exists())

    def test_company_owner_and_company_staff_cannot_set_prices(self):
        company = Company.objects.create(name='Tenant', slug='pricing-tenant')
        role = Role.objects.create(company=company, name='Wildcard', code='ADMIN', permissions=['*'])
        owner = User.objects.create_user(email='tenant-pricing@example.test', company=company, role=role, is_staff=True)
        company.owner = owner
        company.save()
        self.client.force_login(owner)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 403)
        self.assertFalse(ModulePriceLabel.objects.exists())

    def test_rejects_zero_negative_unknown_module_and_unmetered_period(self):
        for changes in [{'per_user_credits':'0'}, {'per_user_credits':'-1'}, {'module':'billing'}, {'module':'unregistered'}, {'billing_type':'per_use'}]:
            self.assertEqual(self.client.post(self.url, self.payload(**changes)).status_code, 400)
        self.assertFalse(ModulePriceLabel.objects.exists())
        self.assertFalse(FeatureBundle.objects.exists())

    @override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False, CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
    def test_owner_sees_platform_quote_and_activation_is_shared_with_staff(self):
        self.client.post(self.url, self.payload())
        company = Company.objects.create(name='Paid Tenant', slug='paid-pricing', require_paid_features=True)
        role = Role.objects.create(company=company, name='Owner', code='OWNER', permissions=['*'])
        owner = User.objects.create_user(email='paid-pricing@example.test', company=company, role=role)
        staff = User.objects.create_user(email='paid-pricing-staff@example.test', company=company, role=role)
        company.owner = owner
        company.save()
        CreditWallet.objects.create(company=company, balance=Decimal('100'))
        api = APIClient()
        api.force_authenticate(owner)
        catalog = api.get('/api/v1/saas/credits/bundles').data
        self.assertEqual(catalog[0]['perUserCredits'], '12.50')
        self.assertEqual(catalog[0]['totalCredits'], '25.00')
        self.assertFalse(user_has_permission(staff, 'jobs.view'))
        self.assertFalse(user_has_permission(owner, 'dashboard.view'))
        purchase = api.post('/api/v1/saas/credits/bundles/purchase', {'bundleId':catalog[0]['id']}, format='json')
        self.assertEqual(purchase.status_code, 200, purchase.data)
        self.assertEqual(purchase.data['chargedCredits'], '25.00')
        self.assertTrue(user_has_permission(staff, 'jobs.view'))
        self.assertFalse(user_has_permission(owner, 'dashboard.view'))
        # Dashboard is an independently priced module, not a free side effect.
        self.client.post(self.url, self.payload(module='dashboard', per_user_credits='2'))
        dashboard = ModulePriceLabel.objects.get(feature__key='dashboard')
        self.assertEqual(api.post('/api/v1/saas/credits/bundles/purchase', {'bundleId':dashboard.bundle_id}, format='json').status_code, 200)
        self.assertTrue(user_has_permission(owner, 'dashboard.view'))
        self.assertTrue(user_has_permission(staff, 'dashboard.view'))
        self.assertIn('dashboard', api.get('/api/v1/saas/credits/access').data['enabledModules'])
        api.force_authenticate(staff)
        self.assertEqual(api.get('/api/v1/saas/credits/overview').status_code, 403)

    def test_managed_price_edit_route_returns_to_pricing_page(self):
        self.client.post(self.url, self.payload())
        mapping = ModulePriceLabel.objects.get(feature__key='jobs')
        result = self.client.get(reverse('panel:featurebundle-edit', args=[mapping.bundle_id]))
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result.url, self.url + '#module-jobs')

    def test_purchased_billing_period_and_original_charge_are_preserved(self):
        self.client.post(self.url, self.payload())
        row = ModulePriceLabel.objects.get(feature__key='jobs')
        company = Company.objects.create(name='Existing', slug='existing-price')
        access = CompanyBundleAccess.objects.create(company=company, bundle=row.bundle,
                    billing_type_at_purchase='monthly', last_price_credits=Decimal('25'))
        self.assertEqual(self.client.post(self.url, self.payload(billing_type='weekly')).status_code, 400)
        self.assertRedirects(self.client.post(self.url, self.payload(per_user_credits='20')), self.url)
        access.refresh_from_db()
        row.bundle.refresh_from_db()
        self.assertEqual(row.bundle.billing_type, 'monthly')
        self.assertEqual(row.bundle.per_user_credits, Decimal('20'))
        self.assertEqual(access.last_price_credits, Decimal('25'))
        self.assertFalse(CreditLedger.objects.exists())

    def test_price_publish_can_activate_an_unpublished_catalogue_module(self):
        group = FeatureGroup.objects.create(key='disabled', label='Disabled')
        feature = PlanFeature.objects.create(key='jobs', group=group, label='Custom Job Cards', is_active=False)
        self.client.post(self.url, self.payload())
        feature.refresh_from_db()
        self.assertTrue(feature.is_active)
        self.assertEqual(feature.label, 'Custom Job Cards')
        self.assertEqual(ModulePriceLabel.objects.get(feature=feature).bundle.name, 'Custom Job Cards')
