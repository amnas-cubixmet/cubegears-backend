from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.accounts.serializers import UserSerializer
from apps.companies.models import Company
from apps.companies.serializers import CompanySerializer
from apps.control_panel.forms import CompanyForm, FeatureBundleForm
from apps.customers.models import Customer
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, PlanFeature, SecurityEvent
from .models import CreditWallet, FeatureBundle, CreditLedger, MonthlyUsageInvoice


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False, CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
                   CREDIT_POSTPAID_FEATURE_BILLING_ENABLED=False, CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=False,
                   STORAGE_MONTHLY_METERED_ENABLED=False)
class OwnerOnboardingTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()
        self.company = Company.objects.create(name='New Workshop', slug='new-workshop', require_paid_features=True)
        self.role = Role.objects.create(company=self.company, name='Super Admin', code='SUPER_ADMIN', permissions=['*'], is_system=True)
        self.owner = User.objects.create_user(email='owner@example.test', name='Owner', company=self.company, role=self.role)
        self.company.owner = self.owner
        self.company.save()
        self.staff = User.objects.create_user(email='staff@example.test', name='Staff', company=self.company, role=self.role)
        self.group = FeatureGroup.objects.create(key='ops', label='Operations')
        self.feature = PlanFeature.objects.create(key='customers', label='Customers', group=self.group)
        self.bundle = FeatureBundle.objects.create(key='customers', name='Customers', price_credits=Decimal('0'), per_user_credits=Decimal('10'), billing_type='permanent')
        self.bundle.features.set([self.feature])
        CreditWallet.objects.create(company=self.company, balance=Decimal('100'))
        self.client.force_authenticate(self.owner)

    def test_new_company_has_no_free_modules_even_when_legacy_enforcement_off(self):
        self.assertTrue(user_has_permission(self.owner, 'billing.edit'))
        for permission in ['dashboard.view', 'attendance.self', 'customers.view', 'settings.view', 'storage.view']:
            self.assertFalse(user_has_permission(self.owner, permission), permission)
        access = self.client.get('/api/v1/saas/credits/access').data
        self.assertTrue(access['enforcementEnabled'])
        self.assertTrue(access['isCompanyOwner'])
        self.assertEqual(access['enabledModules'], [])
        self.assertEqual(self.client.get('/api/v1/dashboard').status_code, 403)
        self.assertEqual(self.client.get('/api/v1/customers').status_code, 403)

    def test_wildcard_staff_cannot_see_or_mutate_any_saas_billing_data(self):
        self.client.force_authenticate(self.staff)
        self.assertFalse(user_has_permission(self.staff, 'billing.edit'))
        for endpoint in ['overview', 'account', 'packages', 'features', 'bundles', 'logs', 'feature-activity', 'monthly-statements', 'usage-invoices']:
            self.assertEqual(self.client.get('/api/v1/saas/credits/' + endpoint).status_code, 403, endpoint)
        for endpoint in ['bundles/purchase', 'bundles/disable', 'features/enable', 'features/disable', 'orders', 'checkout-links', 'verify']:
            self.assertEqual(self.client.post('/api/v1/saas/credits/' + endpoint, {}, format='json').status_code, 403, endpoint)
        self.assertEqual(self.client.get('/api/v1/saas/payments/history').status_code, 403)
        self.assertEqual(self.client.get('/api/v1/saas/credits/access').status_code, 200)

    def test_owner_purchases_for_all_authorized_users_and_off_preserves_records(self):
        result = self.client.post('/api/v1/saas/credits/bundles/purchase', {'bundleId': self.bundle.pk}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['chargedCredits'], '20.00')
        self.assertFalse(user_has_permission(self.owner, 'dashboard.view'))
        self.assertTrue(user_has_permission(self.staff, 'customers.view'))
        row = Customer.objects.create(company=self.company, name='Retained Customer', phone='12345')
        self.assertEqual(self.client.post('/api/v1/saas/credits/bundles/disable', {'bundleId': self.bundle.pk}, format='json').status_code, 200)
        self.assertFalse(user_has_permission(self.staff, 'customers.view'))
        self.assertTrue(Customer.objects.filter(pk=row.pk).exists())
        self.assertEqual(self.client.get('/api/v1/saas/credits/bundles').data[0]['canResume'], True)
        result = self.client.post('/api/v1/saas/credits/bundles/purchase', {'bundleId': self.bundle.pk}, format='json')
        self.assertEqual(result.data['chargedCredits'], '0.00')
        self.assertEqual(CreditLedger.objects.count(), 1)
        self.assertEqual(SecurityEvent.objects.filter(company=self.company, user=self.owner).count(), 3)
        self.assertTrue(user_has_permission(self.staff, 'customers.view'))
        self.role.permissions = ['billing.view', 'billing.edit']
        self.role.save()
        self.assertFalse(user_has_permission(self.staff, 'customers.view'))

    def test_company_api_cannot_reassign_owner_or_disable_paid_gating(self):
        payload = CompanySerializer(self.company, data={'owner': str(self.staff.pk), 'require_paid_features': False}, partial=True)
        self.assertTrue(payload.is_valid(), payload.errors)
        payload.save()
        self.company.refresh_from_db()
        self.assertEqual(self.company.owner_id, self.owner.pk)
        self.assertTrue(self.company.require_paid_features)
        self.assertTrue(UserSerializer(self.owner).data['isCompanyOwner'])
        self.assertFalse(UserSerializer(self.staff).data['isCompanyOwner'])

    def test_owner_cannot_cross_tenants_or_use_inactive_account(self):
        other = Company.objects.create(name='Other', slug='other', owner=self.owner)
        self.owner.company = other
        # Invalid role company membership never grants billing authority.
        self.assertFalse(user_has_permission(self.owner, 'billing.edit'))
        self.owner.company = self.company
        self.company.is_active = False
        self.company.save()
        self.assertEqual(self.client.get('/api/v1/saas/credits/overview').status_code, 403)

    def test_account_inventory_and_monthly_history_are_scoped(self):
        other = Company.objects.create(name='Other', slug='other')
        MonthlyUsageInvoice.objects.create(company=self.company, category='feature', billing_month='2026-09', credits_due=Decimal('8'), status='paid')
        MonthlyUsageInvoice.objects.create(company=self.company, category='storage', billing_month='2026-09', credits_due=Decimal('2'), status='pending')
        MonthlyUsageInvoice.objects.create(company=other, category='feature', billing_month='2026-09', credits_due=Decimal('999'))
        rows = self.client.get('/api/v1/saas/credits/monthly-statements').data
        self.assertEqual(rows[0]['totalCredits'], '10.00')
        self.assertEqual(rows[0]['status'], 'pending')
        with override_settings(TENANT_BASE_DOMAIN='example.test'):
            info = self.client.get('/api/v1/saas/credits/account').data
        self.assertEqual(info['configuredTenantUrl'], 'https://new-workshop.example.test/account')
        self.assertIn('customer', next(r for r in info['modules'] if r['key'] == 'customers')['models'])

    def test_platform_owner_choices_are_tenant_scoped_and_single_module_can_be_priced(self):
        other = Company.objects.create(name='Other', slug='other')
        alien = User.objects.create_user(email='alien@example.test', company=other)
        form = CompanyForm(instance=self.company)
        self.assertNotIn(alien, form.fields['owner'].queryset)
        bundle = FeatureBundleForm(data={'name':'Single feature', 'description':'One module', 'features':[self.feature.pk], 'per_user_credits':'5', 'billing_type':'weekly'})
        self.assertTrue(bundle.is_valid(), bundle.errors)

    @patch('apps.accounts.views.queue_email')
    def test_public_signup_records_owner_without_free_features(self, email):
        self.client.force_authenticate(None)
        response = self.client.post('/api/v1/auth/signup', {
            'workshop_name':'Signup Workshop', 'owner_name':'Signup Owner', 'mobile':'9876543210',
            'email':'new@example.test', 'state':'Kerala', 'city':'Malappuram',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        company = Company.objects.get(slug='signup-workshop')
        self.assertTrue(company.require_paid_features)
        self.assertEqual(company.owner.email, 'new@example.test')
        self.assertEqual(company.plan, 'credits')
        self.assertTrue(response.data['user']['isCompanyOwner'])
        self.assertNotIn('dashboard.view', response.data['user']['effectivePermissions'])
        self.assertNotIn('attendance.self', response.data['user']['effectivePermissions'])

    @override_settings(CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=True, STORAGE_MONTHLY_METERED_ENABLED=True)
    def test_new_metered_statements_do_not_double_count_legacy_invoices(self):
        from .models import FeatureMeterPeriod, StorageMeterPeriod
        FeatureMeterPeriod.objects.create(company=self.company, feature=self.feature, period='2026-09',
                                          accrued_credits=Decimal('3.25'), activation_credits=Decimal('1'))
        StorageMeterPeriod.objects.create(company=self.company, period='2026-09', accrued_credits=Decimal('2.50'))
        MonthlyUsageInvoice.objects.create(company=self.company, category='feature', billing_month='2026-09', credits_due=Decimal('500'))
        result = self.client.get('/api/v1/saas/credits/monthly-statements')
        self.assertEqual(result.data[0]['totalCredits'], '6.75')
        self.assertEqual(result.data[0]['status'], 'pending')
