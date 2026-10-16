"""Account creation -> worker email -> setup -> login -> reset for each scope."""
import re
from urllib.parse import parse_qs, urlsplit
from django.core import mail
from django.core.cache import cache
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.accounts.test_email_helpers import simulate_celery_worker
from apps.roles.models import Role


@override_settings(EMAIL_CELERY_ENABLED=True, CELERY_BROKER_URL='redis://127.0.0.1:6379/0',
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   FRONTEND_URL='https://company.example.test/')
class AccountEmailMatrixTests(TestCase):
    password = 'InitialAccount938!Secure'
    replacement = 'UpdatedAccount492!Secure'

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        simulate_celery_worker(self)
        self.actor = User.objects.create_superuser(email='operator@example.test', name='Operator', password='Operator932!Secure')
        self.api = APIClient()

    def email_link(self, user):
        message = mail.outbox[-1]
        self.assertEqual(message.to, [user.email])
        self.assertEqual(len(message.alternatives), 1)
        self.assertNotIn(self.password, message.body)
        self.assertNotIn(self.replacement, message.body)
        return re.findall(r'https?://[^\s]+', message.body)[-1]

    def identity(self, user):
        user.refresh_from_db()
        return (user.company_id, user.role_id, user.is_staff, user.is_superuser)

    def token_payload(self, user):
        return {'uid': urlsafe_base64_encode(force_bytes(user.pk)),
                'token': default_token_generator.make_token(user), 'password': self.password}

    def finish_workshop_flow(self, user, setup_url):
        expected = self.identity(user)
        parts = urlsplit(setup_url)
        self.assertEqual(parts.netloc, 'company.example.test')
        self.assertEqual(parts.path, '/setup-password')
        query = {key: value[0] for key, value in parse_qs(parts.query).items()}
        setup = self.api.post('/api/v1/auth/setup-password', {**query, 'password':self.password}, format='json')
        self.assertEqual(setup.status_code, 200, setup.data)
        self.assertEqual(setup.data['user']['id'], str(user.pk))
        self.assertEqual(self.identity(user), expected)
        self.assertEqual(self.api.post('/api/v1/auth/setup-password', {**query, 'password':self.password}, format='json').status_code, 400)
        login = self.api.post('/api/v1/auth/login', {'email':user.email.upper(),'password':self.password}, format='json')
        self.assertEqual(login.status_code, 200, login.data)
        with self.captureOnCommitCallbacks(execute=True):
            result = self.api.post('/api/v1/auth/forgot-password', {'email':user.email}, format='json')
        self.assertEqual(result.status_code, 200)
        reset_url = urlsplit(self.email_link(user))
        self.assertEqual(reset_url.netloc, 'company.example.test')
        self.assertEqual(reset_url.path, '/reset-password')
        reset = {key:value[0] for key,value in parse_qs(reset_url.query).items()}
        result = self.api.post('/api/v1/auth/reset-password', {**reset,'password':self.replacement}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertIn('access', result.data)
        self.assertEqual(self.identity(user), expected)
        self.assertEqual(self.api.post('/api/v1/auth/reset-password', {**reset,'password':self.replacement}, format='json').status_code, 400)
        self.assertEqual(self.api.post('/api/v1/auth/login', {'email':user.email,'password':self.password}, format='json').status_code, 400)
        self.assertEqual(self.api.post('/api/v1/auth/login', {'email':user.email,'password':self.replacement}, format='json').status_code, 200)

    def signup_owner(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.api.post('/api/v1/auth/signup', {
                'workshop_name':'Email Workshop','owner_name':'Company Owner','mobile':'9876543210',
                'email':'company-owner@example.test','country':'India','state':'Kerala','city':'Kochi',
                'is_staff':True,'is_superuser':True,
            },format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return User.objects.get(email='company-owner@example.test')

    def test_company_signup_owner_setup_login_and_reset(self):
        owner = self.signup_owner()
        self.assertEqual(owner.company.owner_id, owner.pk)
        self.assertFalse(owner.is_staff)
        self.assertFalse(owner.is_superuser)
        self.finish_workshop_flow(owner, self.email_link(owner))

    def test_company_created_staff_setup_login_and_reset_keeps_role(self):
        owner = self.signup_owner()
        role = Role.objects.create(company=owner.company,name='Staff Reader',code='STAFF_READER',permissions=['staff.view'])
        self.api.force_authenticate(owner)
        with self.captureOnCommitCallbacks(execute=True):
            result = self.api.post('/api/v1/employees',{'name':'Company Staff','email':'company-staff@example.test','phone':'9876543210','roleId':str(role.pk)},format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.api.force_authenticate(None)
        member = User.objects.get(email='company-staff@example.test')
        self.assertEqual(member.role_id, role.pk)
        self.finish_workshop_flow(member, self.email_link(member))
        self.assertFalse(member.is_staff)
        self.assertFalse(member.is_superuser)

    def test_platform_owner_superadmin_admin_staff_create_setup_login_and_reset(self):
        for kind, superuser in [('platform-owner',True),('superadmin',True),('admin-staff',False)]:
            with self.subTest(account=kind):
                cache.clear()
                role = Role.objects.create(name=kind,code=kind.upper(),permissions=['panel.dashboard.view','panel.users.view'])
                self.client.force_login(self.actor)
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(reverse('panel:user-create'),{
                        'email':kind+'@example.test','name':kind,'role':str(role.pk),
                        'is_active':'on','is_staff':'on',**({'is_superuser':'on'} if superuser else {}),
                    })
                self.assertEqual(response.status_code, 302, response.content)
                user = User.objects.get(email=kind+'@example.test')
                expected = self.identity(user)
                setup_url = urlsplit(self.email_link(user))
                self.assertEqual(setup_url.netloc, 'testserver')
                self.assertIn('/login/password-setup/',setup_url.path)
                self.client.logout()
                result = self.client.post(setup_url.path,{'new_password1':self.password,'new_password2':self.password})
                self.assertEqual(result.status_code, 302)
                self.assertEqual(self.client.session['_auth_user_id'], str(user.pk))
                self.assertEqual(self.identity(user), expected)
                self.client.logout()
                result = self.client.post('/login/',{'email':user.email,'password':self.password})
                self.assertEqual(result.status_code,302)
                self.client.logout()
                # Even recovery started on the company API must email the platform route.
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.api.post('/api/v1/auth/forgot-password',{'email':user.email},format='json')
                self.assertEqual(response.status_code,200)
                reset_url = urlsplit(self.email_link(user))
                self.assertIn('/login/password-reset/',reset_url.path)
                result = self.client.post(reset_url.path,{'new_password1':self.replacement,'new_password2':self.replacement})
                self.assertEqual(result.status_code,302)
                self.assertEqual(self.identity(user), expected)
                self.assertTrue(user.check_password(self.replacement))
                self.assertFalse(user.check_password(self.password))
                self.client.logout()
                self.assertEqual(self.client.post('/login/',{'email':user.email,'password':self.replacement}).status_code,302)

    def test_wrong_workshop_setup_endpoint_never_demotes_platform_superadmin(self):
        user = User.objects.create_superuser(email='unset-platform@example.test',name='Platform',password=None)
        user.set_unusable_password(); user.save(update_fields=['password'])
        expected = self.identity(user)
        result = self.api.post('/api/v1/auth/setup-password',self.token_payload(user),format='json')
        self.assertEqual(result.status_code,400)
        self.assertEqual(self.identity(user),expected)
        self.assertFalse(user.has_usable_password())

    def test_wrong_workshop_reset_endpoint_never_changes_platform_password(self):
        expected = self.identity(self.actor)
        result = self.api.post('/api/v1/auth/reset-password',self.token_payload(self.actor),format='json')
        self.assertEqual(result.status_code,400)
        self.assertEqual(self.identity(self.actor),expected)
        self.assertTrue(self.actor.check_password('Operator932!Secure'))

    def test_platform_magic_request_emails_control_panel_link(self):
        with self.captureOnCommitCallbacks(execute=True):
            result = self.api.post('/api/v1/auth/magic-link',{'email':self.actor.email},format='json')
        self.assertEqual(result.status_code,200)
        path = urlsplit(self.email_link(self.actor)).path
        self.assertIn('/login/magic/verify/', path)
        expected = self.identity(self.actor)
        self.assertEqual(self.client.post(path).status_code, 302)
        self.assertEqual(self.identity(self.actor), expected)
        self.assertTrue(self.actor.email_verified)
        self.assertEqual(self.client.session['_auth_user_id'], str(self.actor.pk))
        self.assertNotEqual(self.client.post(path).status_code, 302)
