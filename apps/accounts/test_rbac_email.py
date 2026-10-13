from unittest.mock import patch
from django.core import mail
from django.db import transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.accounts.email import queue_email
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.roles.models import Role
from apps.employees.models import Employee
from apps.jobs.models import Job
from apps.vehicles.models import Vehicle


class PermissionBoundaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='One', slug='one')
        cls.other = Company.objects.create(name='Two', slug='two')
        cls.role = Role.objects.create(company=cls.company, name='Reader', code='READ', permissions=['jobs.view'])
        cls.user = User.objects.create_user(email='staff@example.com', name='Staff', company=cls.company, role=cls.role, password='Secure-pass-928!')
        cls.customer = Customer.objects.create(company=cls.company, name='Customer')
        cls.vehicle = Vehicle.objects.create(company=cls.company, customer=cls.customer, registration='KL01AB1234')
        cls.job = Job.objects.create(company=cls.company, customer=cls.customer, vehicle=cls.vehicle, job_number='JOB-1')

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def grant(self, codes):
        self.role.permissions = codes
        self.role.save()

    def test_staff_flag_does_not_bypass_permissions(self):
        self.user.is_staff = True
        self.user.save()
        self.assertFalse(user_has_permission(self.user, 'payroll.view'))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/').status_code, 403)
        self.assertEqual(self.api.get('/api/v1/payroll/runs').status_code, 403)

    def test_compat_read_write_and_stock_permission(self):
        root = f'/api/v1/job-cards/{self.job.pk}'
        self.assertEqual(self.api.get(root + '/parts/availability').status_code, 200)
        self.assertEqual(self.api.post(root + '/issues', {'lines': []}, format='json').status_code, 403)
        self.grant(['jobs.edit'])
        self.assertEqual(self.api.post(root + '/issues', {'lines': []}, format='json').status_code, 403)
        self.grant(['jobs.edit', 'stock.edit'])
        self.assertEqual(self.api.post(root + '/issues', {'lines': []}, format='json').status_code, 200)

    def test_invalid_or_inactive_role_and_company_fail_closed(self):
        for field, value in [('is_active', False), ('company', self.other), ('permissions', '*')]:
            original = getattr(self.role, field)
            setattr(self.role, field, value)
            self.assertFalse(user_has_permission(self.user, 'jobs.view'))
            setattr(self.role, field, original)
        self.user.company.is_active = False
        self.assertFalse(user_has_permission(self.user, 'jobs.view'))

    def test_cross_company_record_is_not_visible(self):
        self.grant(['customers.view'])
        other_customer = Customer.objects.create(company=self.other, name='Private')
        self.assertEqual(self.api.get(f'/api/v1/customers/{other_customer.pk}').status_code, 404)

    def test_role_manager_cannot_escalate(self):
        self.grant(['company.manage', 'jobs.view'])
        for permissions in [['*'], ['payroll.edit'], ['panel.users.edit'], 'jobs.view']:
            result = self.api.post('/api/v1/roles/', {'name': 'Escalated', 'code': 'BAD', 'permissions': permissions}, format='json')
            self.assertEqual(result.status_code, 400)
        result = self.api.post('/api/v1/roles/', {'name': 'Reader', 'code': 'NEW', 'permissions': ['jobs.view'], 'is_system': True}, format='json')
        self.assertEqual(result.status_code, 201)
        self.assertFalse(Role.objects.get(pk=result.data['id']).is_system)

    def test_staff_editor_cannot_assign_owner_or_link_other_tenant(self):
        self.grant(['staff.edit'])
        Role.objects.create(company=self.company, name='Owner', code='OWNER', permissions=['*'])
        staff = Employee.objects.create(company=self.company, name='Mechanic', employee_code='EMP1')
        result = self.api.patch(f'/api/v1/employees/{staff.pk}', {'role': 'Owner'}, format='json')
        self.assertEqual(result.status_code, 400)
        other_user = User.objects.create_user(email='other@example.com', name='Other', company=self.other)
        result = self.api.patch(f'/api/v1/employees/{staff.pk}', {'user': str(other_user.pk)}, format='json')
        self.assertEqual(result.status_code, 400)

    def test_clearing_employee_role_revokes_login_permissions(self):
        self.grant(['staff.edit', 'jobs.view'])
        lower = Role.objects.create(company=self.company, name='Lower', code='LOW', permissions=['jobs.view'])
        linked = User.objects.create_user(email='linked@example.com', name='Linked', company=self.company, role=lower)
        employee = Employee.objects.create(company=self.company, name='Linked', employee_code='EMP2', user=linked, role_name='Lower')
        result = self.api.patch(f'/api/v1/employees/{employee.pk}', {'role': ''}, format='json')
        self.assertEqual(result.status_code, 200)
        linked.refresh_from_db()
        self.assertIsNone(linked.role)
        self.assertFalse(user_has_permission(linked, 'jobs.view'))

    def test_tenant_wildcard_cannot_access_platform(self):
        self.grant(['*'])
        self.user.is_staff = True
        self.user.save()
        self.assertTrue(user_has_permission(self.user, 'jobs.edit'))
        self.assertFalse(user_has_permission(self.user, 'panel.companies.view'))

    def test_delegated_platform_reader_routes(self):
        role = Role.objects.create(name='Platform reader', code='PLATFORM', permissions=['panel.companies.view'])
        user = User.objects.create_user(email='platform@example.com', name='Platform', is_staff=True, role=role)
        self.client.force_login(user)
        self.assertEqual(self.client.get('/companies/').status_code, 200)
        self.assertEqual(self.client.get('/companies/add/').status_code, 403)
        self.assertEqual(self.client.post('/companies/add/', {}).status_code, 403)
        self.assertEqual(self.client.get('/users/').status_code, 403)
        self.assertNotContains(self.client.get('/companies/'), 'href="/users/"')
        self.assertRedirects(self.client.get('/login/'), '/companies/')

    def test_superuser_and_effective_permissions(self):
        self.assertEqual(self.api.get('/api/v1/auth/me').data['effectivePermissions'], ['jobs.view'])
        self.user.is_superuser = True
        self.user.save()
        self.assertTrue(user_has_permission(self.user, 'panel.users.delete'))
        self.user.is_active = False
        self.assertFalse(user_has_permission(self.user, 'panel.users.delete'))


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class DirectEmailTests(TestCase):
    def test_delivery_after_commit_without_celery(self):
        with patch('apps.accounts.tasks.send_email_task.delay') as celery:
            with self.captureOnCommitCallbacks(execute=True):
                queue_email('Subject', 'Message', ['recipient@example.com'])
            celery.assert_not_called()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['recipient@example.com'])

    def test_rollback_does_not_send(self):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    queue_email('Subject', 'Message', ['recipient@example.com'])
                    raise ValueError('rollback')
            except ValueError:
                pass
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_endpoint_sends_without_worker(self):
        User.objects.create_user(email='reset@example.com', name='Reset', password='Secure-924!')
        with self.captureOnCommitCallbacks(execute=True):
            result = self.client.post('/api/v1/auth/forgot-password', {'email': 'reset@example.com'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('reset-password', mail.outbox[0].body)

    def test_smtp_error_is_logged_without_public_account_disclosure(self):
        with patch('apps.accounts.email.send_mail', side_effect=OSError('SMTP unavailable')):
            with self.assertLogs('django.test', level='ERROR'):
                with self.captureOnCommitCallbacks(execute=True):
                    queue_email('Subject', 'Message', ['recipient@example.com'])
