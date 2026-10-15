from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.roles.models import Role
from apps.saas.models import FeatureGroup, PlanFeature
from apps.credits.models import CompanyBundleAccess, CreditWallet, FeatureBundle, CreditLedger
from .models import Company


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False, CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class ExistingCompanyAccessRolloutTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Existing Workshop', slug='existing-workshop-lock', require_paid_features=False)
        role = Role.objects.create(company=self.company, name='Owner', code='SUPER_ADMIN', permissions=['*'])
        self.owner = User.objects.create_user(email='existing-owner-lock@example.test', company=self.company, role=role)
        self.company.owner = self.owner
        self.company.save()
        group = FeatureGroup.objects.create(key='rollout', label='Workshop')
        self.jobs = PlanFeature.objects.create(key='jobs', label='Job Cards', group=group)
        self.dashboard = PlanFeature.objects.create(key='dashboard', label='Dashboard', group=group)
        CreditWallet.objects.create(company=self.company, balance=Decimal('100'))
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def migrate_policy(self):
        migration = import_module('apps.companies.migrations.0004_lock_existing_company_modules')
        migration.lock_existing_companies(apps, SimpleNamespace(connection=connection))
        self.owner.refresh_from_db()
        self.company.refresh_from_db()

    def test_existing_unpurchased_company_is_locked_even_with_legacy_switches_off(self):
        self.assertTrue(user_has_permission(self.owner, 'jobs.view'))
        self.migrate_policy()
        access = self.api.get('/api/v1/saas/credits/access').data
        self.assertTrue(access['enforcementEnabled'])
        self.assertTrue(access['paidAccessRequired'])
        self.assertTrue(access['isCompanyOwner'])
        self.assertEqual(access['enabledModules'], [])
        self.assertEqual(self.api.get('/api/v1/dashboard').status_code, 403)
        self.assertEqual(self.api.get('/api/v1/jobs').status_code, 403)
        self.assertEqual(self.api.get('/api/v1/saas/credits/overview').status_code, 200)

    def test_rollout_preserves_active_purchases_balance_and_owner(self):
        bundle = FeatureBundle.objects.create(key='rollout-jobs', name='Jobs', price_credits=0, per_user_credits=10)
        bundle.features.add(self.jobs)
        access = CompanyBundleAccess.objects.create(company=self.company, bundle=bundle, enabled=True,
                    billing_type_at_purchase='weekly', valid_until=timezone.now()+timedelta(days=7), last_price_credits=10)
        self.migrate_policy()
        self.migrate_policy()  # Re-running must not create charges or reset access.
        access.refresh_from_db()
        self.assertTrue(access.enabled)
        self.assertEqual(access.last_price_credits, Decimal('10'))
        self.assertTrue(user_has_permission(self.owner, 'jobs.view'))
        self.assertFalse(user_has_permission(self.owner, 'dashboard.view'))
        self.assertEqual(CreditWallet.objects.get(company=self.company).balance, Decimal('100'))
        self.assertEqual(self.company.owner_id, self.owner.pk)
        self.assertFalse(CreditLedger.objects.exists())
