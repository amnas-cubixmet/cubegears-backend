from io import StringIO
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.accounts.test_email_helpers import simulate_celery_worker
from apps.companies.models import Company
from apps.employees.models import Employee
from .models import Role


@override_settings(EMAIL_CELERY_ENABLED=True, CELERY_BROKER_URL='redis://localhost:6379/0',
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FRONTEND_URL='https://workshop.test')
class StaffCompanyRoleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.company = Company.objects.create(name='Company A', slug='staff-company-a')
        self.other = Company.objects.create(name='Company B', slug='staff-company-b')
        owner_role = Role.objects.create(company=self.company, name='Owner', code='SUPER_ADMIN', is_system=True, permissions=['*'])
        self.owner = User.objects.create_user(email='staff-owner@test.example', name='Owner', company=self.company, role=owner_role)
        self.company.owner = self.owner
        self.company.save()
        self.role = Role.objects.create(company=self.company, name='Workshop Reader', code='WORKSHOP_READER', permissions=['jobs.view'])
        self.foreign = Role.objects.create(company=self.other, name='Workshop Reader', code='OTHER_READER', permissions=['jobs.view'])
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def payload(self, **changes):
        return {'name':'New Staff','email':'new-staff@test.example','phone':'9876543210','roleId':str(self.role.pk),**changes}

    def test_role_lists_never_include_global_other_company_or_system_roles(self):
        Role.objects.create(name='Platform', code='PLATFORM', permissions=['panel.users.view'])
        rows = self.api.get('/api/v1/roles/').data
        self.assertEqual({row['id'] for row in rows}, {str(self.role.pk)})
        self.assertEqual(self.api.get(f'/api/v1/roles/{self.foreign.pk}').status_code, 404)

    def test_assignable_roles_exclude_owner_wildcard_inactive_and_foreign_roles(self):
        Role.objects.create(company=self.company, name='Legacy Admin', code='LEGACY', permissions=['*'])
        Role.objects.create(company=self.company, name='Hidden', code='HIDDEN', permissions=['jobs.view'], is_active=False)
        Role.objects.create(company=self.company, name='Billing', code='BILLING', permissions=['billing.view'])
        rows = self.api.get('/api/v1/roles/?assignable=1').data
        self.assertEqual({row['id'] for row in rows}, {str(self.role.pk)})

    def test_staff_creator_can_fetch_only_delegable_company_roles_without_owner_authority(self):
        role = Role.objects.create(company=self.company, name='Profile editor', code='EDITOR', permissions=['staff.create','jobs.view'])
        user = User.objects.create_user(email='profile-editor@test.example', company=self.company, role=role)
        self.api.force_authenticate(user)
        self.assertEqual(self.api.get('/api/v1/roles/?assignable=1').status_code, 200)
        self.assertEqual(self.api.post('/api/v1/roles/', {'name':'Added','code':'NEW','permissions':['jobs.view']}, format='json').status_code, 403)

    def test_foreign_ids_and_per_staff_override_payloads_cannot_create_accounts(self):
        self.assertEqual(self.api.post('/api/v1/employees', self.payload(roleId=str(self.foreign.pk)), format='json').status_code, 400)
        for extra in ({'permissions':['*']}, {'permissionOverrides':{'jobs.view':True}}, {'is_superuser':True}):
            self.assertEqual(self.api.post('/api/v1/employees', self.payload(**extra), format='json').status_code, 400)
        self.assertFalse(User.objects.filter(email='new-staff@test.example').exists())

    def test_duplicate_role_names_require_id_and_preserve_exact_choice(self):
        second = Role.objects.create(company=self.company, name=self.role.name, code='SAME_NAME', permissions=['attendance.self'])
        with patch('config.celery.app.send_task'):
            with self.captureOnCommitCallbacks(execute=True):
                result = self.api.post('/api/v1/employees', self.payload(roleId=str(second.pk)), format='json')
        self.assertEqual(result.status_code, 201, result.data)
        employee = Employee.objects.get(pk=result.data['id'])
        self.assertEqual(employee.user.role_id, second.pk)
        self.assertEqual(result.data['assignedRoleId'], str(second.pk))
        result = self.api.post('/api/v1/employees', self.payload(email='ambiguous@test.example', roleId=None, role=self.role.name), format='json')
        self.assertEqual(result.status_code, 400)

    def test_company_role_editor_rejects_unknown_permissions_and_duplicate_names(self):
        for data in ({'name':'Unknown','code':'UNKNOWN','permissions':['unknown.secret']}, {'name':'workshop reader','code':'DUP','permissions':['jobs.view']}):
            self.assertEqual(self.api.post('/api/v1/roles/', data, format='json').status_code, 400)

    def test_email_worker_setup_and_login_keep_exact_company_role_and_permissions(self):
        simulate_celery_worker(self)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.api.post('/api/v1/employees', self.payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['new-staff@test.example'])
        self.assertIn('Company A', message.body)
        self.assertIn('Assigned role: Workshop Reader', message.body)
        link = next(part for part in message.body.split() if part.startswith('https://workshop.test/setup-password?'))
        params = parse_qs(urlparse(link).query)
        setup = self.api.post('/api/v1/auth/setup-password', {'uid':params['uid'][0], 'token':params['token'][0], 'password':'StaffSafePassword381!'}, format='json')
        self.assertEqual(setup.status_code, 200, setup.data)
        self.assertEqual(setup.data['user']['company']['id'], str(self.company.pk))
        self.assertEqual(setup.data['user']['role']['id'], str(self.role.pk))
        self.assertEqual(setup.data['user']['effectivePermissions'], ['jobs.view'])
        staff = User.objects.get(email='new-staff@test.example')
        self.assertTrue(staff.email_verified)
        self.assertFalse(staff.is_staff)
        self.assertFalse(staff.is_superuser)
        self.api.force_authenticate(None)
        login = self.api.post('/api/v1/auth/login', {'email':staff.email, 'password':'StaffSafePassword381!'}, format='json')
        self.assertEqual(login.status_code, 200, login.data)
        self.api.credentials(HTTP_AUTHORIZATION='Bearer ' + login.data['access'])
        self.assertEqual(self.api.get('/api/v1/jobs').status_code, 200)
        self.assertEqual(self.api.get('/api/v1/roles/').status_code, 403)
        self.assertEqual(self.api.get('/api/v1/saas/credits/overview').status_code, 403)
        self.assertEqual(self.api.get('/api/v1/auth/setup-password').status_code, 405)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend', EMAIL_HOST='smtp.test', EMAIL_HOST_USER='test', EMAIL_HOST_PASSWORD='test-only', DEFAULT_FROM_EMAIL='app@test.example')
    def test_email_diagnostic_queues_via_celery_instead_of_request_smtp(self):
        with patch('config.celery.app.send_task') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                call_command('check_email_delivery', to='staff@test.example', stdout=StringIO())
        enqueue.assert_called_once()
        self.assertFalse(getattr(mail, 'outbox', []))

    def test_renaming_role_preserves_staff_identity_and_resend_uses_that_role(self):
        simulate_celery_worker(self)
        with self.captureOnCommitCallbacks(execute=True):
            result = self.api.post('/api/v1/employees', self.payload(), format='json')
        self.assertEqual(result.status_code, 201)
        employee = Employee.objects.get(pk=result.data['id'])
        self.assertEqual(self.api.patch(f'/api/v1/roles/{self.role.pk}', {'name':'Renamed Reader'}, format='json').status_code, 200)
        self.assertEqual(self.api.get(f'/api/v1/employees/{employee.pk}').data['role'], 'Renamed Reader')
        with self.captureOnCommitCallbacks(execute=True):
            response = self.api.post(f'/api/v1/employees/{employee.pk}/invite', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('Assigned role: Renamed Reader', mail.outbox[-1].body)
        employee.user.refresh_from_db()
        self.assertEqual(employee.user.role_id, self.role.pk)
