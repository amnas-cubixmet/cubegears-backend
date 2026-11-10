"""Real ORM search authorization, tenancy, input and navigation regressions."""
from datetime import date
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.vehicles.models import Vehicle
from apps.jobs.models import Job
from apps.employees.models import Employee
from apps.inventory.models import StockItem, Supplier, SupplierBill, PurchaseOrder, WorkshopFinancialAccount
from apps.services.models import Service
from apps.expenses.models import Expense
from apps.invoices.models import Invoice
from apps.payments.models import Payment
from apps.roles.models import Role


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class WorkspaceSearchTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Search Workshop", slug="search-ws")
        self.other = Company.objects.create(name="Other Workshop", slug="search-other-ws")
        perms = [
            "customers.view", "vehicles.view", "jobs.view", "staff.view",
            "stock.view", "invoices.view", "payments.view", "services.view", "expenses.view",
        ]
        self.role = Role.objects.create(
            company=self.company, name="Owner", code="SEARCH_OWNER",
            permissions=perms, is_active=True,
        )
        self.owner = User.objects.create_user(
            email="search-owner@example.test", password="strong-test-pass",
            name="Search Owner", company=self.company, role=self.role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.limited_role = Role.objects.create(
            company=self.company, name="Jobs Only", code="SEARCH_JOBS_ONLY",
            permissions=["jobs.view"], is_active=True,
        )
        self.limited = User.objects.create_user(
            email="search-limited@example.test", password="strong-test-pass",
            name="Jobs Only", company=self.company, role=self.limited_role,
        )
        self.customer = Customer.objects.create(
            company=self.company, name="Aisha Unique", phone="+919876543210",
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, customer=self.customer, registration="KL10AB7890",
            make="Toyota", model="Etios",
        )
        self.job = Job.objects.create(
            company=self.company, customer=self.customer, vehicle=self.vehicle,
            job_number="JOB-SEARCH-222", status="Inspection",
            complaints=[{"description": "Gear noise special query"}],
        )
        self.employee = Employee.objects.create(
            company=self.company, name="Manu Mechanic", phone="+919111111111",
            employee_code="STAFF-SEARCH-11",
        )
        self.supplier = Supplier.objects.create(
            company=self.company, name="Kerala Auto Supplier",
            contact_person="Supply Person", phone="+919222222222",
        )
        self.stock = StockItem.objects.create(
            company=self.company, name="Engine Air Filter", sku="STOCK-SEARCH-888",
        )
        self.invoice = Invoice.objects.create(
            company=self.company, customer=self.customer, number="INV-SEARCH-555",
            date=date(2026, 11, 5), kind="invoice",
        )
        self.payment = Payment.objects.create(
            company=self.company, customer=self.customer, invoice=self.invoice,
            date=date(2026, 11, 5), reference="UPI-SEARCH-999",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def found(self, q):
        reply = self.client.get("/api/v1/search", {"q": q})
        self.assertEqual(reply.status_code, 200, getattr(reply, "data", None))
        self.assertEqual(reply["Cache-Control"], "private, no-store")
        return reply.data["results"]

    def test_each_resource_searches_real_database_and_returns_existing_routes(self):
        examples = (
            ("Aisha Unique", "Customers", f"/customers/{self.customer.pk}"),
            ("9876543210", "Customers", f"/customers/{self.customer.pk}"),
            ("KL10AB7890", "Vehicles", f"/vehicles/{self.vehicle.pk}"),
            ("JOB-SEARCH-222", "Job Cards", f"/jobs/{self.job.pk}"),
            ("special query", "Complaints", f"/jobs/{self.job.pk}/complaints"),
            ("Manu Mechanic", "Staff", f"/staff-management/staff/{self.employee.pk}"),
            ("Kerala Auto Supplier", "Suppliers", f"/suppliers/{self.supplier.pk}"),
            ("STOCK-SEARCH-888", "Inventory", "/stock?search="),
            ("INV-SEARCH-555", "Invoices", f"/invoices/{self.invoice.pk}"),
            ("UPI-SEARCH-999", "Payments", f"/payments/{self.payment.pk}"),
        )
        for query, group, path in examples:
            with self.subTest(group=group):
                found = self.found(query)
                self.assertTrue(any(r["group"] == group and r["route"].startswith(path) for r in found), found)

    def test_additional_workshop_modules_and_direct_supplier_navigation(self):
        service = Service.objects.create(
            company=self.company, name="Wheel Alignment", code="SERV-SEARCH-444",
        )
        expense = Expense.objects.create(
            company=self.company, date=date(2026, 11, 8), category="Utilities",
            vendor="Electricity Vendor Search", reference="EXP-SEARCH-11",
        )
        account = WorkshopFinancialAccount.objects.create(
            company=self.company, name="Bank Search Account", kind="Bank",
        )
        bill = SupplierBill.objects.create(
            company=self.company, supplier=self.supplier,
            invoice_number="BILL-SEARCH-777", invoice_date=date(2026, 11, 8),
        )
        purchase = PurchaseOrder.objects.create(
            company=self.company, supplier=self.supplier, number="PO-SEARCH-333",
        )
        examples = (
            ("SERV-SEARCH-444", "Services", f"/services/{service.pk}"),
            ("EXP-SEARCH-11", "Expenses", f"/expenses/{expense.pk}"),
            ("Bank Search Account", "Accounts", f"/finance/accounts/{account.pk}"),
            ("BILL-SEARCH-777", "Supplier Bills", f"/suppliers/{bill.supplier_id}"),
            ("PO-SEARCH-333", "Purchases", f"/suppliers/{purchase.supplier_id}"),
        )
        for query, group, route in examples:
            with self.subTest(group=group):
                found = self.found(query)
                self.assertTrue(any(row["group"] == group and row["route"] == route for row in found), found)

        self.client.force_authenticate(user=self.limited)
        for query, _, _ in examples:
            self.assertEqual(self.found(query), [])
        # The supplier record remains searchable for staff with stock view
        # without exposing the financial documents associated with it.
        self.limited_role.permissions = ["stock.view"]
        self.limited_role.save(update_fields=["permissions"])
        self.client.force_authenticate(user=self.limited)
        self.assertTrue(any(r["group"] == "Suppliers" for r in self.found("Kerala Auto Supplier")))
        self.assertFalse(any(r["group"] in {"Supplier Bills", "Purchases"} for r in self.found("SEARCH")))

    def test_live_tenant_isolation(self):
        outsider = Customer.objects.create(
            company=self.other, name="SECRET SEARCH CUSTOMER", phone="+919444444444",
        )
        vehicle = Vehicle.objects.create(
            company=self.other, customer=outsider, registration="KL19SECRETX",
        )
        Job.objects.create(
            company=self.other, customer=outsider, vehicle=vehicle,
            job_number="JOB-SECRET-999",
        )
        self.assertEqual(self.found("SECRET SEARCH"), [])
        self.assertEqual(self.found("KL19SECRETX"), [])
        self.assertEqual(self.found("JOB-SECRET-999"), [])
        self.assertTrue(self.found("KL10AB7890"))

    def test_permissions_limit_all_groups_and_phone_search(self):
        self.client.force_authenticate(user=self.limited)
        self.assertEqual(self.found("9876543210"), [])
        self.assertEqual(self.found("STOCK-SEARCH-888"), [])
        self.assertEqual(self.found("INV-SEARCH-555"), [])
        results = self.found("JOB-SEARCH-222")
        self.assertEqual({r["group"] for r in results}, {"Job Cards"})
        self.assertFalse(any("phone" in str(r).lower() for r in results))

    def test_login_and_invalid_search_inputs(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get("/api/v1/search", {"q": "Aisha"}).status_code, 401)
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.found("A"), [])
        self.assertEqual(self.client.get("/api/v1/search", {"q": "a" * 101}).status_code, 400)
        for limit in ("wrong", "0", "11"):
            self.assertEqual(self.client.get("/api/v1/search", {"q": "JOB", "limit": limit}).status_code, 400)

    def test_group_limits_and_no_duplicate_identifiers(self):
        for i in range(12):
            Customer.objects.create(company=self.company, name=f"Search Match {i}", phone=f"+91980000{i:05d}")
        response = self.client.get("/api/v1/search", {"q": "Search Match", "limit": "3"})
        self.assertEqual(response.status_code, 200)
        records = [r for r in response.data["results"] if r["group"] == "Customers"]
        self.assertEqual(len(records), 3)
        self.assertEqual(len({r["id"] for r in records}), len(records))
