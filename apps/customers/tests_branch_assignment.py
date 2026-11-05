"""Customer branch select: real tenant data, writable selection, isolation."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class CustomerBranchIntegrationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Customer Branch Company", slug="customer-branch-one")
        self.foreign = Company.objects.create(name="Another Company", slug="customer-branch-two")
        role = Role.objects.create(
            company=self.company, name="Customer Admin", code="CUSTOMER_BRANCH_ADMIN",
            permissions=["customers.view", "customers.create", "customers.edit"],
            is_active=True,
        )
        self.owner = User.objects.create_user(
            company=self.company, role=role, email="customer-branches@example.test",
            password="test-password", name="Workshop Owner",
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.branch = Branch.objects.create(
            company=self.company, name="South Service Center", code="SOUTH", is_active=True,
        )
        self.second = Branch.objects.create(
            company=self.company, name="North Service Center", code="NORTH", is_active=True,
        )
        self.hidden = Branch.objects.create(
            company=self.company, name="Disabled Branch", code="DISABLED", is_active=False,
        )
        self.outside = Branch.objects.create(
            company=self.foreign, name="Outside Branch", code="OUTSIDE", is_active=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_real_tenant_branch_choices_and_customer_create_selection(self):
        options = self.client.get("/api/v1/customers/branch-options")
        self.assertEqual(options.status_code, 200, options.data)
        self.assertEqual(
            {row["name"] for row in options.data},
            {"South Service Center", "North Service Center"},
        )
        record = self.client.post("/api/v1/customers", {
            "name": "New Customer", "phone": "+919777777777", "branch": str(self.branch.pk),
        }, format="json")
        self.assertEqual(record.status_code, 201, record.data)
        customer = Customer.objects.get(pk=record.data["id"])
        self.assertEqual(customer.branch_id, self.branch.pk)
        self.assertEqual(record.data["branchId"], str(self.branch.pk))
        self.assertEqual(record.data["branchName"], self.branch.name)

    def test_branch_filter_is_server_scoped_and_invalid_values_fail_closed(self):
        Customer.objects.create(
            company=self.company, branch=self.branch, name="South Customer", phone="+919666666666",
        )
        Customer.objects.create(
            company=self.company, branch=self.second, name="North Customer", phone="+919555555555",
        )
        Customer.objects.create(
            company=self.foreign, branch=self.outside, name="Foreign Customer", phone="+919444444444",
        )
        scoped = self.client.get("/api/v1/customers", {"branch": str(self.branch.pk)})
        self.assertEqual(scoped.status_code, 200, scoped.data)
        rows = scoped.data if isinstance(scoped.data, list) else scoped.data["results"]
        self.assertEqual([row["name"] for row in rows], ["South Customer"])
        bad = self.client.get("/api/v1/customers", {"branch": "unsafe"})
        self.assertEqual(bad.status_code, 200)
        self.assertEqual(
            bad.data if isinstance(bad.data, list) else bad.data["results"], [],
        )

    def test_cross_company_branch_is_rejected_on_create_and_update(self):
        foreign = self.client.post("/api/v1/customers", {
            "name": "Wrong Branch", "phone": "+919333333333", "branch": str(self.outside.pk),
        }, format="json")
        self.assertEqual(foreign.status_code, 400, foreign.data)
        self.assertFalse(Customer.objects.filter(name="Wrong Branch").exists())
        valid = self.client.post("/api/v1/customers", {
            "name": "Allowed Customer", "phone": "+919222222222", "branch": str(self.branch.pk),
        }, format="json")
        self.assertEqual(valid.status_code, 201, valid.data)
        update = self.client.patch(f"/api/v1/customers/{valid.data['id']}", {
            "branch": str(self.outside.pk),
        }, format="json")
        self.assertEqual(update.status_code, 400, update.data)
        self.assertEqual(Customer.objects.get(pk=valid.data["id"]).branch_id, self.branch.pk)
