"""Customer branch select: real tenant data, writable selection, isolation."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.employees.models import Employee
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

    def test_staff_only_sees_and_uses_its_authorized_branches(self):
        staff = User.objects.create_user(
            company=self.company, role=self.owner.role, branch=self.branch,
            email="limited-customer-staff@example.test", password="test-password",
            name="Branch Staff",
        )
        south = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="South Private Customer", phone="+919555555550",
        )
        north = Customer.objects.create(
            company=self.company, branch=self.second,
            name="North Private Customer", phone="+919555555551",
        )
        self.client.force_authenticate(user=staff)
        choices = self.client.get("/api/v1/customers/branch-options")
        self.assertEqual([row["id"] for row in choices.data], [str(self.branch.pk)])
        listing = self.client.get("/api/v1/customers")
        data = listing.data if isinstance(listing.data, list) else listing.data["results"]
        self.assertEqual([row["name"] for row in data], ["South Private Customer"])
        self.assertEqual(self.client.get(f"/api/v1/customers/{north.pk}").status_code, 404)
        denied = self.client.post("/api/v1/customers", {
            "name": "Unauthorized Customer", "phone": "+919555555552",
            "branch": str(self.second.pk),
        }, format="json")
        self.assertEqual(denied.status_code, 400, denied.data)
        self.assertFalse(Customer.objects.filter(name="Unauthorized Customer").exists())
        created = self.client.post("/api/v1/customers", {
            "name": "Auto Assigned Customer", "phone": "+919555555553",
        }, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(Customer.objects.get(pk=created.data["id"]).branch_id, self.branch.pk)
        self.assertEqual(self.client.patch(f"/api/v1/customers/{south.pk}", {
            "branch": str(self.second.pk),
        }, format="json").status_code, 400)
        self.assertEqual(self.client.patch(f"/api/v1/customers/{south.pk}", {
            "branch": None,
        }, format="json").status_code, 400)

        staff.allowed_branches.add(self.second)
        options = self.client.get("/api/v1/customers/branch-options")
        self.assertEqual({item["id"] for item in options.data}, {
            str(self.branch.pk), str(self.second.pk),
        })
        self.assertEqual(options.data[0]["id"], str(self.branch.pk))
        self.assertEqual(self.client.get(f"/api/v1/customers/{north.pk}").status_code, 200)
        reassigned = self.client.patch(f"/api/v1/customers/{south.pk}", {
            "branch": str(self.second.pk),
        }, format="json")
        self.assertEqual(reassigned.status_code, 200, reassigned.data)

    def test_staff_cannot_read_duplicate_customer_contact_from_another_branch(self):
        staff = User.objects.create_user(
            company=self.company, role=self.owner.role, branch=self.branch,
            email="privacy-staff@example.test", password="test-password", name="Privacy Staff",
        )
        Customer.objects.create(
            company=self.company, branch=self.second,
            name="Private North Contact", phone="+919555555558",
        )
        self.client.force_authenticate(user=staff)
        result = self.client.post("/api/v1/customers/check-duplicate", {
            "phone": "+919555555558",
        }, format="json")
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.data["duplicate"])
        self.assertIsNone(result.data["customer"])

    def test_only_owner_can_grant_one_to_three_active_staff_branches(self):
        staff = User.objects.create_user(
            company=self.company, role=self.owner.role, branch=self.branch,
            email="multi-branch-staff@example.test", password="test-password",
            name="Limited Technician",
        )
        profile = Employee.objects.create(
            company=self.company, branch=self.branch, user=staff,
            name="Limited Technician", phone="+919555559999",
            employee_code="EMP900", role_name="Customer Admin",
        )
        endpoint = f"/api/v1/employees/{profile.pk}"
        selected = [str(self.branch.pk), str(self.second.pk)]
        updated = self.client.patch(endpoint, {
            "allowedBranchIds": selected,
        }, format="json")
        self.assertEqual(updated.status_code, 200, getattr(updated, "data", None))
        staff.refresh_from_db()
        self.assertEqual(set(staff.allowed_branches.values_list("pk", flat=True)), {
            self.branch.pk, self.second.pk,
        })
        self.assertEqual(set(updated.data["assignedBranchIds"]), set(selected))
        incorrect = self.client.patch(endpoint, {
            "allowedBranchIds": [str(self.second.pk)],
        }, format="json")
        self.assertEqual(incorrect.status_code, 400)
        foreign = self.client.patch(endpoint, {
            "allowedBranchIds": [str(self.branch.pk), str(self.outside.pk)],
        }, format="json")
        self.assertEqual(foreign.status_code, 400)
        self.client.force_authenticate(user=staff)
        forbidden = self.client.patch(endpoint, {
            "allowedBranchIds": [str(self.branch.pk), str(self.second.pk)],
        }, format="json")
        self.assertIn(forbidden.status_code, (400, 403))
