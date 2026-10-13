from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.employees.models import Employee
from apps.inventory.models import StockItem
from apps.invoices.models import Invoice
from apps.jobs.models import Job
from apps.saas.models import Subscription
from apps.roles.models import Role
from apps.vehicles.models import Vehicle
from .forms import CustomerForm, UserForm
from .urls import urlpatterns


class PanelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(email='admin@example.com', name='Admin', password='Test-admin-823!')
        cls.company = Company.objects.create(name='Workshop', slug='workshop')
        cls.customer = Customer.objects.create(company=cls.company, name='Customer', phone='1234567890')
        cls.vehicle = Vehicle.objects.create(company=cls.company, customer=cls.customer, registration='KL01AA1234')
        cls.job = Job.objects.create(company=cls.company, customer=cls.customer, vehicle=cls.vehicle, job_number='JOB-1')
        cls.records = {
            'company': cls.company, 'user': cls.admin,
            'branch': Branch.objects.create(company=cls.company, name='Main', code='MAIN'),
            'role': Role.objects.create(company=cls.company, name='Mechanic', code='mechanic'),
            'vehicle': cls.vehicle,
            'customer': cls.customer, 'job': cls.job,
            'subscription': Subscription.objects.create(company=cls.company),
            'stock': StockItem.objects.create(company=cls.company, name='Part', sku='PART-1'),
            'invoice': Invoice.objects.create(company=cls.company, customer=cls.customer, number='INV-1', date=date.today()),
            'staff': Employee.objects.create(company=cls.company, name='Mechanic', employee_code='EMP-1'),
        }

    def setUp(self):
        self.client.force_login(self.admin)

    def test_root_routes_and_api(self):
        self.assertEqual(reverse('panel:dashboard'), '/')
        self.assertEqual(reverse('panel:login'), '/login/')
        self.assertEqual(self.client.get('/panel/').status_code, 404)
        self.assertEqual(self.client.get('/api/v1/health/').status_code, 200)
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_every_panel_get_route_renders(self):
        for route in urlpatterns:
            name = route.name
            kwargs = {}
            if '<uuid:pk>' in str(route.pattern):
                kwargs['pk'] = self.records[name.split('-')[0]].pk
            with self.subTest(route=name):
                response = self.client.get(reverse('panel:' + name, kwargs=kwargs))
                expected = 405 if name == 'logout' else 403 if name.endswith('-toggle') else 302 if name in ('login', 'user-delete') else 200
                self.assertEqual(response.status_code, expected)

    def test_all_routes_require_login_and_reject_regular_users(self):
        regular = User.objects.create_user(email='regular@example.com', name='Regular', password='Test-regular-923!')
        for route in urlpatterns:
            if route.name == 'login':
                continue
            kwargs = {'pk': self.records[route.name.split('-')[0]].pk} if '<uuid:pk>' in str(route.pattern) else {}
            url = reverse('panel:' + route.name, kwargs=kwargs)
            with self.subTest(route=route.name):
                self.client.logout()
                self.assertEqual(self.client.get(url).status_code, 302)
                self.client.force_login(regular)
                self.assertEqual(self.client.get(url).status_code, 405 if route.name == "logout" else 403)

    def test_login_redirect_is_safe_and_preserves_filters(self):
        self.client.logout()
        response = self.client.get('/users/?company=' + str(self.company.pk))
        self.assertIn('/login/?next=', response.url)
        for target, expected in [('https://evil.example/', '/'), ('//evil.example/', '/'), ('/users/?q=Admin', '/users/?q=Admin')]:
            self.client.logout()
            response = self.client.post('/login/', {'email': self.admin.email, 'password': 'Test-admin-823!'}, QUERY_STRING='next=' + target)
            self.assertRedirects(response, expected, fetch_redirect_response=False)

    def test_logout_requires_post(self):
        self.assertEqual(self.client.get('/logout/').status_code, 405)
        self.assertRedirects(self.client.post('/logout/'), '/login/')
        self.assertRedirects(self.client.get('/'), '/login/?next=/')

    def test_invalid_filter_and_pagination(self):
        self.assertEqual(self.client.get('/users/?company=invalid').status_code, 400)
        Company.objects.bulk_create([Company(name=f'Extra {i}', slug=f'extra-{i}') for i in range(55)])
        response = self.client.get('/companies/?q=Extra&page=2')
        self.assertEqual(len(response.context['rows']), 5)
        self.assertContains(response, 'q=Extra')
        self.assertEqual(self.client.get('/companies/?page=invalid').status_code, 200)

    def test_company_create_edit_toggle_delete(self):
        data = {'name': 'New', 'slug': 'new', 'country': 'India', 'currency': 'INR', 'plan': 'Starter', 'is_active': 'on'}
        self.assertRedirects(self.client.post('/companies/add/', data), '/companies/')
        company = Company.objects.get(slug='new')
        data['name'] = 'Renamed'
        self.assertRedirects(self.client.post(reverse('panel:company-edit', args=[company.pk]), data), '/companies/')
        company.refresh_from_db()
        self.assertEqual(company.name, 'Renamed')
        self.assertRedirects(self.client.post(reverse('panel:company-toggle', args=[company.pk]), {'next': 'https://evil.example/'}), '/companies/')
        company.refresh_from_db()
        self.assertFalse(company.is_active)
        self.assertRedirects(self.client.post(reverse('panel:company-delete', args=[company.pk])), '/companies/')
        self.assertFalse(Company.objects.filter(pk=company.pk).exists())

    def test_admin_self_protection(self):
        data = {'email': self.admin.email, 'name': 'Admin'}
        self.assertRedirects(self.client.post(reverse('panel:user-edit', args=[self.admin.pk]), data), '/users/')
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active and self.admin.is_staff and self.admin.is_superuser)
        self.assertEqual(self.client.post(reverse('panel:user-toggle', args=[self.admin.pk])).status_code, 403)
        self.client.post(reverse('panel:user-delete', args=[self.admin.pk]))
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_password_and_tenant_validation(self):
        form = UserForm(data={'email': 'new@example.com', 'name': 'New'})
        self.assertFalse(form.is_valid())
        self.assertIn('password', form.errors)
        form = UserForm(data={'email': 'new@example.com', 'name': 'New', 'password': '123'})
        self.assertFalse(form.is_valid())
        self.assertIn('password', form.errors)
        other = Company.objects.create(name='Other', slug='other')
        branch = Branch.objects.create(company=other, name='Other branch')
        form = CustomerForm(data={'company': self.company.pk, 'branch': branch.pk, 'name': 'Customer'})
        self.assertFalse(form.is_valid())
        self.assertIn('branch', form.errors)
        form = CustomerForm(data={'company': other.pk, 'name': 'Customer'}, instance=self.customer)
        self.assertFalse(form.is_valid())
        self.assertIn('company', form.errors)

    def test_mrr_mixed_cycles(self):
        Subscription.objects.create(company=self.company, amount=Decimal('1200'), billing_cycle='annual')
        Subscription.objects.create(company=self.company, amount=Decimal('500'), billing_cycle='monthly')
        response = self.client.get('/')
        self.assertEqual(response.context['estimated_mrr'], Decimal('600'))

    def test_supporting_resource_crud(self):
        cases = [
            ('branch', 'branches', Branch, {'company': self.company.pk, 'name': 'Second', 'code': 'SECOND', 'is_active': 'on'}),
            ('role', 'roles', Role, {'company': self.company.pk, 'name': 'Manager', 'code': 'manager', 'permissions': ['jobs.view'], 'is_active': 'on'}),
            ('vehicle', 'vehicles', Vehicle, {'company': self.company.pk, 'customer': self.customer.pk, 'registration': 'KL10AA5678', 'odometer': '0', 'status': 'Active'}),
        ]
        for singular, resource, model, data in cases:
            with self.subTest(resource=resource):
                before = set(model.objects.values_list('pk', flat=True))
                self.assertRedirects(self.client.post(reverse(f'panel:{singular}-create'), data), f'/{resource}/')
                obj = model.objects.exclude(pk__in=before).get()
                self.assertRedirects(self.client.post(reverse(f'panel:{singular}-edit', args=[obj.pk]), data), f'/{resource}/')
                self.assertRedirects(self.client.post(reverse(f'panel:{singular}-delete', args=[obj.pk])), f'/{resource}/')
                self.assertFalse(model.objects.filter(pk=obj.pk).exists())

    def test_protected_delete_leaves_customer_intact(self):
        self.assertRedirects(self.client.post(reverse('panel:customer-delete', args=[self.customer.pk])), '/customers/')
        self.assertTrue(Customer.objects.filter(pk=self.customer.pk).exists())
