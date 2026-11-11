"""Customer list aggregates: tenant-safe, role-safe, no per-customer API fanout."""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.invoices.models import Invoice
from apps.jobs.models import Job
from apps.roles.models import Role
from apps.vehicles.models import Vehicle


@override_settings(
    CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
    CREDIT_LABEL_ONLY_ACCESS_ENABLED=False,
)
class CustomerDirectorySummaryTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Customer Summary Workshop", slug="summary-one")
        self.foreign = Company.objects.create(name="Foreign Summary Workshop", slug="summary-two")
        self.branch = Branch.objects.create(
            company=self.company, name="Main Branch", code="MAIN", is_active=True,
        )
        self.owner_role = Role.objects.create(
            company=self.company, code="CUSTOMER_SUMMARY_OWNER", name="Summary Owner",
            permissions=["customers.view", "vehicles.view", "jobs.view", "invoices.view"],
            is_active=True,
        )
        self.owner = User.objects.create_user(
            company=self.company, role=self.owner_role, email="summary-owner@example.test",
            password="test-password", name="Summary Owner",
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.staff_role = Role.objects.create(
            company=self.company, code="CUSTOMER_SUMMARY_STAFF", name="Contact Only",
            permissions=["customers.view"], is_active=True,
        )
        self.staff = User.objects.create_user(
            company=self.company, role=self.staff_role, email="summary-staff@example.test",
            password="test-password", name="Contacts Only",
        )
        self.customer = Customer.objects.create(
            company=self.company, branch=self.branch, name="Customer Alpha", phone="+919999999991",
        )
        self.other = Customer.objects.create(
            company=self.company, branch=self.branch, name="Customer Beta", phone="+919999999992",
        )
        self.foreign_customer = Customer.objects.create(
            company=self.foreign, name="Foreign Customer", phone="+919999999993",
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            registration="KL-10-AB-1234", vin="TESTVIN123",
        )
        Job.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            vehicle=self.vehicle, job_number="JOB-ALPHA-1001",
        )
        Invoice.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            number="SUMMARY-INVOICE-01", kind="invoice", status="Partially Paid",
            finalized_at=timezone.now(), date=date(2026, 11, 11),
            total=Decimal("118.00"), paid=Decimal("20.00"), balance=Decimal("98.00"),
        )
        Invoice.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            number="SUMMARY-DRAFT-01", kind="invoice", status="Draft",
            date=date(2026, 11, 11), total=Decimal("500.00"),
            paid=Decimal("0.00"), balance=Decimal("500.00"),
        )
        Invoice.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            number="SUMMARY-EST-01", kind="estimate", status="Finalized",
            finalized_at=timezone.now(), date=date(2026, 11, 11),
            total=Decimal("400.00"), balance=Decimal("400.00"),
        )
        Invoice.objects.create(
            company=self.foreign, customer=self.foreign_customer,
            number="FOREIGN-SUMMARY-01", kind="invoice", status="Finalized",
            finalized_at=timezone.now(), date=date(2026, 11, 11),
            total=Decimal("999.00"), balance=Decimal("999.00"),
        )
        self.client = APIClient()

    def test_bulk_summary_returns_only_tenant_posted_financials_and_real_counts(self):
        self.client.force_authenticate(self.owner)
        result = self.client.get("/api/v1/customers/directory-summary")
        self.assertEqual(result.status_code, 200, result.data)
        records = {row["customerId"]: row for row in result.data["results"]}
        self.assertEqual(set(records), {str(self.customer.pk), str(self.other.pk)})
        alpha = records[str(self.customer.pk)]
        self.assertEqual(alpha["vehicleCount"], 1)
        self.assertEqual(alpha["jobsCount"], 1)
        self.assertEqual(alpha["totalSpent"], "20.00")
        self.assertEqual(alpha["outstanding"], "98.00")
        self.assertEqual(alpha["totalInvoiced"], "118.00")
        self.assertTrue(alpha["moneyAvailable"])
        self.assertEqual(records[str(self.other.pk)]["outstanding"], "0.00")
        self.assertEqual(records[str(self.other.pk)]["vehicleCount"], 0)

    def test_contact_only_staff_never_receive_hidden_financial_or_vehicle_data(self):
        self.client.force_authenticate(self.staff)
        result = self.client.get("/api/v1/customers/directory-summary")
        self.assertEqual(result.status_code, 200, result.data)
        alpha = next(row for row in result.data["results"]
                     if row["customerId"] == str(self.customer.pk))
        self.assertIsNone(alpha["vehicleCount"])
        self.assertIsNone(alpha["jobsCount"])
        self.assertIsNone(alpha["totalSpent"])
        self.assertIsNone(alpha["outstanding"])
        self.assertFalse(alpha["vehiclesAvailable"])
        self.assertFalse(alpha["jobsAvailable"])
        self.assertFalse(alpha["moneyAvailable"])
        hidden = self.client.get("/api/v1/customers", {"search": "KL-10-AB-1234"})
        self.assertEqual(hidden.status_code, 200, hidden.data)
        self.assertEqual(hidden.data if isinstance(hidden.data, list) else hidden.data["results"], [])

    def test_server_search_by_registration_vin_and_job_requires_matching_permissions(self):
        self.client.force_authenticate(self.owner)
        for query in ("KL-10-AB-1234", "TESTVIN123", "JOB-ALPHA-1001"):
            with self.subTest(query=query):
                result = self.client.get("/api/v1/customers", {"search": query})
                self.assertEqual(result.status_code, 200, result.data)
                rows = result.data if isinstance(result.data, list) else result.data["results"]
                self.assertEqual([row["id"] for row in rows], [str(self.customer.pk)])
        tenant = self.client.get("/api/v1/customers/directory-summary", {"search": "Foreign"})
        self.assertEqual(tenant.status_code, 200, tenant.data)
        self.assertEqual(tenant.data["results"], [])
