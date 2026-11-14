"""Posted invoice summary is the single read-only source for Job Card collections."""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.invoices.models import Invoice
from apps.jobs.models import Job
from apps.roles.models import Role
from apps.vehicles.models import Vehicle


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
                   CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class JobBillingSummaryTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Job Billing Garage", slug="job-billing-garage")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        self.owner_role = Role.objects.create(
            company=self.company, name="Owner", code="JOB_BILL_OWNER",
            permissions=["*"], is_active=True,
        )
        self.owner = User.objects.create_user(
            email="job-billing-owner@example.test", password="test-only",
            name="Owner", company=self.company, branch=self.branch, role=self.owner_role,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(
            company=self.company, branch=self.branch, name="Customer", phone="9876543101",
        )
        self.vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch,
            customer=self.customer, registration="KL10AB2345",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch, customer=self.customer,
            vehicle=self.vehicle, job_number="JOB-BILL-001",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = f"/api/v1/jobs/{self.job.pk}/billing-summary"

    def invoice(self, *, number, status, total, taxable, paid, balance,
                finalized=True, via_source=False):
        from django.utils import timezone
        return Invoice.objects.create(
            company=self.company, branch=self.branch,
            number=number, kind="invoice", status=status,
            date=date(2026, 11, 14), customer=self.customer, vehicle=self.vehicle,
            job=None if via_source else self.job,
            source_job_id=str(self.job.pk) if via_source else "",
            taxable=Decimal(taxable), total=Decimal(total),
            paid=Decimal(paid), balance=Decimal(balance),
            finalized_at=timezone.now() if finalized else None,
        )

    def test_posted_invoices_are_aggregated_without_draft_or_duplicate_receipts(self):
        first = self.invoice(number="INV-BILL-001", status="Finalized", total="1180",
                             taxable="1000", paid="300", balance="880")
        second = self.invoice(number="INV-BILL-002", status="Partially Paid", total="590",
                              taxable="500", paid="200", balance="390", via_source=True)
        self.invoice(number="INV-BILL-003", status="Draft", total="5000",
                     taxable="5000", paid="0", balance="5000", finalized=False)
        self.invoice(number="INV-BILL-004", status="Cancelled", total="2500",
                     taxable="2500", paid="0", balance="2500")
        result = self.client.get(self.url)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["postedInvoiceCount"], 2)
        self.assertEqual(result.data["invoiceCount"], 3)
        self.assertEqual(result.data["draftInvoiceCount"], 1)
        self.assertEqual(result.data["invoiceTotal"], "1770.00")
        self.assertEqual(result.data["sellingBeforeGST"], "1500.00")
        self.assertEqual(result.data["paid"], "500.00")
        self.assertEqual(result.data["outstanding"], "1270.00")
        self.assertEqual(set(result.data["postedInvoiceIds"]), {str(first.pk), str(second.pk)})

    def test_only_drafts_means_no_income_or_payment_due(self):
        self.invoice(number="INV-ONLY-DRAFT", status="Draft", total="1000",
                     taxable="1000", paid="0", balance="1000", finalized=False)
        result = self.client.get(self.url)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["source"], "not_invoiced")
        self.assertIsNone(result.data["invoiceTotal"])
        self.assertIsNone(result.data["paid"])
        self.assertIsNone(result.data["outstanding"])

    def test_jobs_view_without_invoice_permission_denied(self):
        role = Role.objects.create(
            company=self.company, name="Mechanic", code="JOB_BILL_READER",
            permissions=["jobs.view"], is_active=True,
        )
        worker = User.objects.create_user(
            email="job-only@example.test", password="test-only", name="Mechanic",
            company=self.company, branch=self.branch, role=role,
        )
        self.client.force_authenticate(worker)
        result = self.client.get(self.url)
        self.assertEqual(result.status_code, 403)

    def test_company_scope_prevents_other_workshop_job_access(self):
        company = Company.objects.create(name="Other Garage", slug="other-job-billing-garage")
        branch = Branch.objects.create(company=company, name="Main", code="MAIN")
        role = Role.objects.create(company=company, name="Owner", code="OTHER_JOB_OWNER",
                                   permissions=["*"], is_active=True)
        owner = User.objects.create_user(
            email="other-job-owner@example.test", password="test-only", name="Owner",
            company=company, branch=branch, role=role,
        )
        company.owner = owner
        company.save(update_fields=["owner"])
        self.client.force_authenticate(owner)
        self.assertEqual(self.client.get(self.url).status_code, 404)
