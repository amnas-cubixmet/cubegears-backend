from datetime import datetime, time
from decimal import Decimal
from unittest.mock import patch

from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.companies.models import Company
from apps.employees.models import Employee
from apps.saas.models import MediaFile, StorageEvent, FeatureGroup, PlanFeature
from .models import (BillingCyclePolicy, CompanyStorageState, StorageCreditPolicy,
                     StorageMeterPeriod, StorageMeterCursor, FeatureCreditPrice, FeatureMeterPeriod,
                     CompanyPostpaidFeature, CreditWallet)
from .monthly_storage import meter_company_storage
from .monthly_features import activate_postpaid_feature, deactivate_postpaid_feature
from .billing_cycle_forms import BillingCyclePolicyForm
from .calendar_billing import finalize_month, settle_statement
from .tasks import run_configured_billing

GB = 1024 ** 3


def dt(month, day, hour=0, minute=0):
    return timezone.make_aware(datetime(2026, month, day, hour, minute))


@override_settings(STORAGE_MONTHLY_METERED_ENABLED=True,
                   CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED=True,
                   CREDIT_FEATURE_ENFORCEMENT_ENABLED=True,
                   CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
                   CREDIT_POSTPAID_FEATURE_BILLING_ENABLED=False,
                   CREDIT_BILLING_CELERY_ENABLED=True)
class RuntimeBillingTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Event Garage', slug='event-garage')
        self.policy = BillingCyclePolicy.objects.create(enabled=True, event_tracking_enabled=True,
            due_day=5, due_time=time(14, 30), effective_month='2026-10')
        self.storage_policy = StorageCreditPolicy.objects.create(is_active=True, credits_per_gb=31)
        group = FeatureGroup.objects.create(key='event-features', label='Events')
        self.feature = PlanFeature.objects.create(key='jobs', label='Jobs', group=group)
        FeatureCreditPrice.objects.create(feature=self.feature, per_staff_credits=31, billing_period='monthly')

    def test_same_name_resize_accrues_old_and_new_bytes_without_repeated_sums(self):
        with patch('django.utils.timezone.now', return_value=dt(10, 1)):
            media = MediaFile.objects.create(company=self.company, name='same.bin', file='same.bin', size_bytes=12*GB)
        with patch('django.utils.timezone.now', return_value=dt(10, 16)):
            media.size_bytes = 10*GB
            media.save(update_fields=['size_bytes'])
        self.assertEqual(CompanyStorageState.objects.get(company=self.company).used_bytes, 10*GB)
        self.assertEqual(StorageEvent.objects.filter(event_type='REPLACE').get().delta_bytes, -2*GB)
        with patch('django.utils.timezone.now', return_value=dt(11, 1)), CaptureQueriesContext(connection) as queries:
            meter_company_storage(self.company)
            meter_company_storage(self.company)  # replay is idempotent
        self.assertFalse(any('SUM(' in q['sql'].upper() for q in queries))
        self.assertEqual(StorageMeterPeriod.objects.get(period='2026-10').accrued_credits, Decimal('340'))

    def test_unchanged_storage_charges_again_next_month_and_delete_stops_future_charge(self):
        with patch('django.utils.timezone.now', return_value=dt(10, 1)):
            media = MediaFile.objects.create(company=self.company, name='x', size_bytes=GB)
        with patch('django.utils.timezone.now', return_value=dt(11, 1)):
            meter_company_storage(self.company)
        with patch('django.utils.timezone.now', return_value=dt(12, 1)):
            meter_company_storage(self.company)
            MediaFile.objects.filter(pk=media.pk).delete()
        self.assertEqual(StorageMeterPeriod.objects.get(period='2026-10').accrued_credits, Decimal('31'))
        self.assertEqual(StorageMeterPeriod.objects.get(period='2026-11').accrued_credits, Decimal('31'))
        self.assertEqual(CompanyStorageState.objects.get().used_bytes, 0)
        with patch('django.utils.timezone.now', return_value=dt(12, 2)):
            meter_company_storage(self.company)
        self.assertEqual(StorageMeterPeriod.objects.get(period='2026-12').accrued_credits, 0)

    def test_rollback_restores_file_counter_and_accrual_together(self):
        with self.assertRaises(ValueError), transaction.atomic(), patch('django.utils.timezone.now', return_value=dt(10, 1)):
            MediaFile.objects.create(company=self.company, name='rollback', size_bytes=GB)
            raise ValueError('rollback')
        self.assertFalse(MediaFile.objects.exists())
        self.assertFalse(CompanyStorageState.objects.exists())
        self.assertFalse(StorageEvent.objects.exists())

    def test_active_staff_changes_reprice_at_event_time_and_off_stops_charge(self):
        with patch('django.utils.timezone.now', return_value=dt(10, 1)):
            Employee.objects.create(company=self.company, employee_code='1', name='One')
            activate_postpaid_feature(self.company, self.feature)
        with patch('django.utils.timezone.now', return_value=dt(10, 16)):
            Employee.objects.create(company=self.company, employee_code='2', name='Two')
        with patch('django.utils.timezone.now', return_value=dt(11, 1)):
            deactivate_postpaid_feature(self.company, self.feature)
        self.assertEqual(FeatureMeterPeriod.objects.get(period='2026-10').accrued_credits, Decimal('47'))
        with patch('django.utils.timezone.now', return_value=dt(11, 2)):
            run_configured_billing()
        self.assertFalse(FeatureMeterPeriod.objects.filter(period='2026-11').exists())

    def test_calculation_and_storage_intervals_are_honoured_from_database(self):
        self.policy.calculation_interval_seconds = 3600
        self.policy.storage_tracking_interval_seconds = 3600
        self.policy.save()
        with patch('django.utils.timezone.now', return_value=dt(10, 1)):
            MediaFile.objects.create(company=self.company, name='x', size_bytes=GB)
            Employee.objects.create(company=self.company, employee_code='1', name='One')
            activate_postpaid_feature(self.company, self.feature)
        with patch('django.utils.timezone.now', return_value=dt(10, 1, 0, 30)):
            run_configured_billing()
        self.assertEqual(StorageMeterCursor.objects.get().sampled_at, dt(10, 1))
        self.assertEqual(CompanyPostpaidFeature.objects.get().sampled_at, dt(10, 1))
        with patch('django.utils.timezone.now', return_value=dt(10, 1, 1)):
            run_configured_billing()
        self.assertEqual(StorageMeterCursor.objects.get().sampled_at, dt(10, 1, 1))
        self.assertEqual(CompanyPostpaidFeature.objects.get().sampled_at, dt(10, 1, 1))

    def test_deduction_waits_for_custom_local_time_and_retries_once(self):
        with patch('django.utils.timezone.now', return_value=dt(10, 1)):
            MediaFile.objects.create(company=self.company, name='x', size_bytes=GB)
        with patch('django.utils.timezone.now', return_value=dt(11, 1)):
            meter_company_storage(self.company)
            _, statement = finalize_month(self.company, '2026-10')
        self.assertEqual(statement.due_at, dt(11, 5, 14, 30))
        CreditWallet.objects.create(company=self.company, balance=100)
        self.assertEqual(settle_statement(statement, at=dt(11, 5, 14, 29))[0], 'not_due')
        self.assertEqual(settle_statement(statement, at=dt(11, 5, 14, 30))[0], 'paid')
        self.assertEqual(settle_statement(statement, at=dt(11, 5, 14, 31))[0], 'already_paid')
        self.assertEqual(CreditWallet.objects.get().balance, Decimal('69'))

    def test_custom_rate_duration_is_snapshotted_and_form_rejects_invalid_intervals(self):
        self.policy.rate_duration_seconds = 86400
        self.policy.save()
        self.storage_policy.credits_per_gb = 1
        self.storage_policy.save()
        with patch('django.utils.timezone.now', return_value=dt(10, 1)):
            MediaFile.objects.create(company=self.company, name='x', size_bytes=GB)
        with patch('django.utils.timezone.now', return_value=dt(10, 2)):
            meter_company_storage(self.company)
        self.assertEqual(StorageMeterPeriod.objects.get().accrued_credits, Decimal('1'))
        form = BillingCyclePolicyForm(data={'due_day':5, 'calculation_interval_seconds':0,
                                          'storage_tracking_interval_seconds':1, 'rate_duration_seconds':10})
        self.assertFalse(form.is_valid())
        self.assertIn('calculation_interval_seconds', form.errors)
        self.assertIn('storage_tracking_interval_seconds', form.errors)
        self.assertIn('rate_duration_seconds', form.errors)

    def test_zero_staff_customer_records_and_account_deactivation(self):
        from apps.accounts.models import User
        from apps.customers.models import Customer
        from .service import billable_user_count, staff_count
        with patch('django.utils.timezone.now', return_value=dt(10, 1)):
            activate_postpaid_feature(self.company, self.feature)
            Customer.objects.create(company=self.company, name='Customer', phone='123')
            user = User.objects.create_user(company=self.company, email='seat@example.test', name='Seat')
            Employee.objects.create(company=self.company, employee_code='1', name='Seat', user=user)
        self.assertEqual(billable_user_count(self.company), 1)
        self.assertEqual(staff_count(self.company), 1)
        with patch('django.utils.timezone.now', return_value=dt(10, 16)):
            user.is_active = False
            user.save(update_fields=['is_active'])
        self.assertEqual(staff_count(self.company), 0)
        self.assertEqual(billable_user_count(self.company), 0)
        with patch('django.utils.timezone.now', return_value=dt(11, 1)):
            run_configured_billing()
        self.assertEqual(FeatureMeterPeriod.objects.get(period='2026-10').accrued_credits, Decimal('15'))
        self.assertFalse(CompanyPostpaidFeature.objects.get().suspended)

    def test_multi_file_queryset_delete_keeps_counter_and_cursor_consistent(self):
        with patch('django.utils.timezone.now', return_value=dt(10, 1)):
            MediaFile.objects.create(company=self.company, name='a', size_bytes=GB)
            MediaFile.objects.create(company=self.company, name='b', size_bytes=2*GB)
        with patch('django.utils.timezone.now', return_value=dt(10, 2)):
            MediaFile.objects.filter(company=self.company).delete()
        self.assertEqual(CompanyStorageState.objects.get().used_bytes, 0)
        self.assertEqual(StorageMeterCursor.objects.get().used_bytes, 0)
        self.assertEqual(StorageEvent.objects.filter(event_type='DELETE').count(), 2)
        self.assertEqual(StorageMeterPeriod.objects.get().accrued_credits, Decimal('3'))

    def test_policy_duration_edit_checkpoints_old_rate_before_new_rate(self):
        with patch('django.utils.timezone.now', return_value=dt(10, 1)):
            MediaFile.objects.create(company=self.company, name='x', size_bytes=GB)
        form = BillingCyclePolicyForm(instance=self.policy, data={
            'enabled':'on', 'event_tracking_enabled':'on', 'due_day':5, 'due_time':'14:30',
            'rate_duration_seconds':86400, 'calculation_interval_seconds':300,
            'storage_tracking_interval_seconds':300})
        self.assertTrue(form.is_valid(), form.errors)
        with patch('django.utils.timezone.now', return_value=dt(10, 2)):
            form.save()
        with patch('django.utils.timezone.now', return_value=dt(10, 3)):
            meter_company_storage(self.company)
        # 31/month for the first day = 1, then 31/day for the next day.
        self.assertEqual(StorageMeterPeriod.objects.get().accrued_credits, Decimal('32'))

    def test_replacement_api_preserves_name_uses_real_bytes_and_is_tenant_scoped(self):
        import tempfile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from rest_framework.test import APIClient
        from apps.accounts.models import User
        from apps.roles.models import Role
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            role = Role.objects.create(company=self.company, name='Manager', code='ADMIN', permissions=['storage.manage', 'storage.view'])
            user = User.objects.create_user(company=self.company, role=role, email='replace@example.test', name='Manager')
            client = APIClient()
            client.force_authenticate(user)
            result = client.post('/api/v1/saas/storage/files', {'files':[SimpleUploadedFile('same.png', b'123456', content_type='image/png')]}, format='multipart')
            self.assertEqual(result.status_code, 201, result.data)
            media = MediaFile.objects.get()
            old_name, storage = media.file.name, media.file.storage
            with self.captureOnCommitCallbacks(execute=True):
                result = client.patch(f'/api/v1/saas/storage/files/{media.pk}', {'file':SimpleUploadedFile('same.png', b'12', content_type='image/png'), 'size_bytes':99999}, format='multipart')
            self.assertEqual(result.status_code, 200, result.data)
            media.refresh_from_db()
            self.assertEqual(media.name, 'same.png')
            self.assertEqual(media.size_bytes, 2)
            self.assertFalse(storage.exists(old_name))
            self.assertEqual(CompanyStorageState.objects.get().used_bytes, 2)
            self.assertEqual(StorageEvent.objects.filter(event_type='REPLACE').count(), 1)
            other = Company.objects.create(name='Other', slug='other-replace')
            role = Role.objects.create(company=other, name='Manager', code='ADMIN', permissions=['storage.manage'])
            other_user = User.objects.create_user(company=other, role=role, email='other-replace@example.test', name='Other')
            client.force_authenticate(other_user)
            result = client.patch(f'/api/v1/saas/storage/files/{media.pk}', {'file':SimpleUploadedFile('same.png', b'123', content_type='image/png')}, format='multipart')
            self.assertEqual(result.status_code, 404)
            self.assertEqual(CompanyStorageState.objects.get(company=self.company).used_bytes, 2)
