from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.roles.models import Role
from apps.companies.models import Company
from apps.saas.models import PlanFeature, FeatureGroup
from .models import (MonthlyBillingPolicy, MonthlyBillingCycle, StorageCreditPolicy,
    StorageMeterCursor, StorageMeterPeriod, CreditWallet, CreditLedger)
from .monthly_policy import billing_status, deadline, retry_company_bills
from .monthly_storage import settle_company_storage
from .service import adjust_company_balance


def point(month, day, hour=0):
    return timezone.make_aware(datetime(2026, month, day, hour), timezone.get_default_timezone())


@override_settings(STORAGE_MONTHLY_METERED_ENABLED=True, CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=False,
                   CREDIT_POSTPAID_FEATURE_BILLING_ENABLED=False)
class MonthlyCollectionTests(TestCase):
    def setUp(self):
        self.config = MonthlyBillingPolicy.objects.create(pk=1, enabled=True, due_day=5)
        self.company = Company.objects.create(name='Monthly Shop', slug='monthly-shop', require_paid_features=True)
        role = Role.objects.create(company=self.company, code='OWNER', name='Owner', permissions=['*'])
        self.owner = User.objects.create_user(email='monthly-owner@test.example', company=self.company, role=role)
        self.company.owner = self.owner
        self.company.save()
        self.staff = User.objects.create_user(email='monthly-staff@test.example', company=self.company, role=role)
        CreditWallet.objects.create(company=self.company, balance=0)
        StorageCreditPolicy.objects.create(is_active=True, billing_mode='snapshot', billing_period='monthly', credits_per_gb=30)
        StorageMeterCursor.objects.create(company=self.company, sampled_at=point(11, 1))
        self.period = StorageMeterPeriod.objects.create(company=self.company, period='2026-10', accrued_credits=30, covered_seconds=100)

    def test_collection_waits_and_freezes_deadline_no_early_charge(self):
        status, amount = settle_company_storage(self.company, '2026-10', at=point(11, 1))
        self.assertEqual(status, 'not_due')
        self.assertFalse(CreditLedger.objects.exists())
        self.assertEqual(MonthlyBillingCycle.objects.get().due_at, point(11, 5))
        self.config.due_day = 10
        self.config.save()
        self.assertEqual(deadline('2026-10').due_at, point(11, 5))
        self.assertEqual(deadline('2026-11').due_at, point(12, 10))

    def test_zero_wallet_is_allowed_through_due_day_and_locked_next_day(self):
        with patch('django.utils.timezone.now', return_value=point(11, 5, 23)):
            self.assertFalse(billing_status(self.company)['blocked'])
            self.assertTrue(user_has_permission(self.owner, 'dashboard.view'))
        with patch('django.utils.timezone.now', return_value=point(11, 6)):
            self.assertTrue(billing_status(self.company)['blocked'])
            self.assertFalse(user_has_permission(self.owner, 'dashboard.view'))
            self.assertFalse(user_has_permission(self.staff, 'dashboard.view'))
            self.assertTrue(user_has_permission(self.owner, 'billing.view'))
            self.assertFalse(user_has_permission(self.staff, 'billing.view'))

    def test_due_charge_retries_after_topup_once_and_restores_access(self):
        with patch('django.utils.timezone.now', return_value=point(11, 6)):
            self.assertEqual(settle_company_storage(self.company, '2026-10')[0], 'insufficient_credits')
            adjust_company_balance(self.company, Decimal('50'), reason='Verified test credit', actor=None, reference='test-topup')
            self.period.refresh_from_db()
            self.assertIsNotNone(self.period.settled_at)
            self.assertFalse(billing_status(self.company)['blocked'])
            retry_company_bills(self.company)
            self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal('20'))
            self.assertEqual(CreditLedger.objects.filter(kind='storage_charge').count(), 1)

    def test_overdue_api_gate_preserves_owner_logs_storage_and_payment_routes(self):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION='Bearer ' + str(RefreshToken.for_user(self.owner).access_token))
        with patch('django.utils.timezone.now', return_value=point(11, 6)):
            # JWT expiry validation uses real epoch time, so use session auth for middleware testing.
            api.credentials()
            api.force_login(self.owner)
            api.force_authenticate(self.owner)
            self.assertEqual(api.get('/api/v1/dashboard').status_code, 402)
            self.assertEqual(api.get('/api/v1/saas/credits/access').status_code, 200)
            self.assertEqual(api.get('/api/v1/saas/storage').status_code, 200)
            self.assertEqual(api.get('/api/v1/saas/storage/events').status_code, 200)
            self.assertEqual(api.post('/api/v1/saas/storage/files/delete', {'ids': []}, format='json').status_code, 402)
            api.force_login(self.staff)
            api.force_authenticate(self.staff)
            self.assertEqual(api.get('/api/v1/saas/storage/events').status_code, 403)

    def test_read_only_status_does_not_create_deadlines_or_charge_and_tenant_isolation(self):
        self.assertEqual(billing_status(self.company, at=point(11, 6))['outstandingCredits'], '30.00')
        self.assertFalse(MonthlyBillingCycle.objects.exists())
        other = Company.objects.create(name='Other', slug='other-monthly')
        self.assertFalse(billing_status(other, at=point(11, 6))['blocked'])
        self.assertFalse(CreditLedger.objects.exists())

    def test_platform_controls_validate_due_day_and_tenant_cannot_edit(self):
        admin = User.objects.create_superuser(email='platform-monthly@example.test', password='secure-test-password')
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse('panel:credits')), 'Monthly usage payment settings')
        self.client.post(reverse('panel:credits'), {'action':'monthly_policy','enabled':'on','due_day':32,'lock_after_due':'on'})
        self.config.refresh_from_db()
        self.assertEqual(self.config.due_day, 5)
        self.assertRedirects(self.client.post(reverse('panel:credits'), {'action':'monthly_policy','enabled':'on','due_day':7,'feature_meter_interval_seconds':300,'lock_after_due':'on'}), reverse('panel:credits'))
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse('panel:credits'), {'action':'monthly_policy','due_day':1}).status_code, 403)

    @override_settings(CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=True, CREDIT_FEATURE_ENFORCEMENT_ENABLED=True)
    def test_feature_payment_grace_and_atomic_suspension(self):
        from .models import CompanyPostpaidFeature, FeatureMeterPeriod
        from .monthly_features import settle_postpaid_feature
        group = FeatureGroup.objects.create(key='monthly', label='Monthly')
        feature = PlanFeature.objects.create(key='customers', label='Customers', group=group)
        state = CompanyPostpaidFeature.objects.create(company=self.company, feature=feature, enabled=True, sampled_at=point(11, 1), monthly_rate=30)
        FeatureMeterPeriod.objects.create(company=self.company, feature=feature, period='2026-10', accrued_credits=30)
        self.assertEqual(settle_postpaid_feature(self.company, feature, '2026-10', at=point(11, 5))[0], 'insufficient_credits')
        state.refresh_from_db()
        self.assertFalse(state.suspended)
        self.assertEqual(settle_postpaid_feature(self.company, feature, '2026-10', at=point(11, 6))[0], 'insufficient_credits')
        state.refresh_from_db()
        self.assertTrue(state.suspended)

    @override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False)
    def test_delete_preserves_storage_charge_and_filename_and_changes_future_usage(self):
        from apps.saas.models import MediaFile, StorageEvent
        from .monthly_storage import meter_company_storage
        self.company.require_paid_features = False
        self.company.save()
        self.period.delete()
        cursor = StorageMeterCursor.objects.get(company=self.company)
        cursor.sampled_at = point(10, 15)
        cursor.used_bytes = 1024 ** 3
        cursor.billable_gb = 1
        cursor.rate_per_gb = 30
        cursor.save()
        media = MediaFile.objects.create(company=self.company, file='test/storage.bin', name='invoice-photo.png', size_bytes=1024**3)
        api = APIClient()
        api.force_authenticate(self.owner)
        with patch('django.utils.timezone.now', return_value=point(10, 15, 1)):
            response = api.post('/api/v1/saas/storage/files/delete', {'ids':[str(media.pk)]}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            row = StorageMeterPeriod.objects.get(company=self.company, period='2026-10')
            self.assertEqual(row.accrued_credits.quantize(Decimal('.01')), Decimal('0.04'))
            before = row.accrued_credits
            cursor.refresh_from_db()
            self.assertEqual(cursor.used_bytes, 0)
            self.assertEqual(StorageEvent.objects.get(event_type='DELETE').name_snapshot, 'invoice-photo.png')
        with patch('django.utils.timezone.now', return_value=point(10, 15, 2)):
            meter_company_storage(self.company)
        row.refresh_from_db()
        self.assertEqual(row.accrued_credits, before)

    def test_short_month_due_day_clamps_and_lock_can_be_disabled(self):
        self.config.due_day = 31
        self.config.lock_after_due = False
        self.config.save()
        self.assertEqual(timezone.localdate(deadline('2026-01').due_at).isoformat(), '2026-02-28')
        self.assertFalse(billing_status(self.company, at=point(12, 1))['blocked'])

    @override_settings(STORAGE_MONTHLY_METERED_ENABLED=False, CREDIT_POSTPAID_FEATURE_BILLING_ENABLED=True)
    def test_legacy_sessions_create_visible_debt_even_before_scheduler_runs(self):
        from .models import FeatureUsageSession, MonthlyUsageInvoice
        from .postpaid import settle_monthly_usage
        group = FeatureGroup.objects.create(key='legacy-due', label='Legacy monthly')
        feature = PlanFeature.objects.create(key='jobs', label='Jobs', group=group)
        FeatureUsageSession.objects.create(company=self.company, feature=feature, started_at=point(10, 1), ended_at=point(11, 1), monthly_credits=30)
        self.assertTrue(billing_status(self.company, at=point(11, 6))['blocked'])
        self.assertFalse(MonthlyUsageInvoice.objects.exists())
        self.assertEqual(settle_monthly_usage(self.company, 'feature', '2026-10', now=point(11, 1))[0], 'not_due')
        self.assertEqual(MonthlyUsageInvoice.objects.get().credits_due, Decimal('30'))
        with patch('django.utils.timezone.now', return_value=point(11, 6)):
            adjust_company_balance(self.company, Decimal('50'), reason='Verified legacy topup', actor=None, reference='legacy-topup')
            self.assertFalse(billing_status(self.company)['blocked'])
            self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal('20'))

    def test_jwt_gate_blocks_dashboard_but_keeps_billing_access(self):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION='Bearer ' + str(RefreshToken.for_user(self.owner).access_token))
        with patch('django.utils.timezone.now', return_value=point(11, 6)):
            self.assertEqual(api.get('/api/v1/dashboard').status_code, 402)
            self.assertEqual(api.get('/api/v1/saas/credits/access').status_code, 200)

    @override_settings(CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=True, CREDIT_FEATURE_ENFORCEMENT_ENABLED=True)
    def test_admin_published_postpaid_module_activates_with_zero_wallet(self):
        from .module_pricing import ModulePriceForm, save_module_price
        from .models import CompanyPostpaidFeature
        admin = User.objects.create_superuser(email='postpaid-pricing@test.example', password='safe-test-password')
        form = ModulePriceForm({'module':'customers','per_user_credits':'15','billing_type':'monthly','monthly_postpaid':'on','published':'on'})
        self.assertTrue(form.is_valid(), form.errors)
        bundle = save_module_price(form.cleaned_data, admin)
        api = APIClient()
        api.force_authenticate(self.owner)
        result = api.post('/api/v1/saas/credits/bundles/purchase', {'bundleId':bundle.pk}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['billingMode'], 'monthly_postpaid')
        self.assertTrue(result.data['bundle']['active'])
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal('0'))
        self.assertTrue(CompanyPostpaidFeature.objects.get(company=self.company, feature__key='customers').enabled)
        self.assertTrue(user_has_permission(self.owner, 'customers.view'))
        self.assertTrue(user_has_permission(self.staff, 'customers.view'))
        self.assertFalse(CreditLedger.objects.exists())
        response = api.post('/api/v1/saas/credits/bundles/disable', {'bundleId':bundle.pk}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(user_has_permission(self.owner, 'customers.view'))

    @override_settings(CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=True, CREDIT_FEATURE_ENFORCEMENT_ENABLED=True)
    def test_configured_feature_cycle_waits_but_switch_off_flushes_partial_usage(self):
        from .models import CompanyPostpaidFeature, FeatureCreditPrice, FeatureMeterPeriod
        from .monthly_features import meter_postpaid_feature, deactivate_postpaid_feature
        from datetime import timedelta
        self.config.feature_meter_interval_seconds = 600
        self.config.save()
        group = FeatureGroup.objects.create(key='cycle-price', label='Cycle')
        feature = PlanFeature.objects.create(key='jobs', label='Jobs', group=group)
        FeatureCreditPrice.objects.create(feature=feature, per_user_credits=15, billing_period='monthly')
        state = CompanyPostpaidFeature.objects.create(company=self.company, feature=feature, enabled=True, sampled_at=point(10, 15), monthly_rate=30)
        at = point(10, 15) + timedelta(minutes=5)
        self.assertEqual(meter_postpaid_feature(state, at=at)[0], 'not_due')
        self.assertFalse(FeatureMeterPeriod.objects.exists())
        deactivate_postpaid_feature(self.company, feature, at=at)
        self.assertGreater(FeatureMeterPeriod.objects.get(period='2026-10').accrued_credits, 0)
        state.refresh_from_db()
        self.assertFalse(state.enabled)

    @override_settings(CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=True, CREDIT_FEATURE_ENFORCEMENT_ENABLED=True)
    def test_storage_debt_pauses_feature_accrual_and_topup_restores_it_without_billing_downtime(self):
        from .models import CompanyPostpaidFeature, FeatureCreditPrice, FeatureMeterReading
        from .monthly_features import meter_postpaid_feature
        group = FeatureGroup.objects.create(key='paused', label='Paused')
        feature = PlanFeature.objects.create(key='customers', label='Customers', group=group)
        FeatureCreditPrice.objects.create(feature=feature, per_user_credits=15, billing_period='monthly')
        state = CompanyPostpaidFeature.objects.create(company=self.company, feature=feature, enabled=True, sampled_at=point(11, 6), monthly_rate=30)
        with patch('django.utils.timezone.now', return_value=point(11, 6, 1)):
            self.assertEqual(meter_postpaid_feature(state)[0], 'payment_blocked')
            self.assertFalse(FeatureMeterReading.objects.exists())
            state.refresh_from_db()
            self.assertTrue(state.suspended)
            adjust_company_balance(self.company, Decimal('50'), reason='Settle storage arrears', actor=None, reference='restore-feature-topup')
            state.refresh_from_db()
            self.assertFalse(state.suspended)
            self.assertEqual(state.sampled_at, point(11, 6, 1))
