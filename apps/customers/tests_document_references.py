"""Customer documents tab saves reference metadata, not file bytes."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.roles.models import Role


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False)
class CustomerDocumentReferenceTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Document Test Workshop", slug="customer-doc-test")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN", is_active=True)
        role = Role.objects.create(
            company=self.company, name="Owner", code="CUSTOMER_DOC_OWNER",
            permissions=["customers.view", "customers.edit"], is_active=True,
        )
        self.owner = User.objects.create_user(
            company=self.company, branch=self.branch, role=role,
            email="document-owner@example.test", password="test-password",
            name="Owner",
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Document Customer", phone="+919876543210",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.url = f"/api/v1/customers/{self.customer.pk}"

    def test_document_reference_survives_reload(self):
        document = {
            "id": "DOC-001", "name": "Vehicle Insurance",
            "type": "Insurance", "vehicleReg": "KL10AB1234",
            "expiryDate": "2027-10-01", "status": "Valid",
            "fileName": "insurance.pdf", "uploadedDate": "2026-11-14",
        }
        response = self.client.patch(self.url, {"documents": [document]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.documents, [document])
        detail = self.client.get(self.url)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["documents"][0]["name"], "Vehicle Insurance")

    def test_malformed_or_large_references_rejected(self):
        for records in [[{"name": ""}], ["not a record"],
                        [{"name": "x" * 190}], [{"name": "ok"}] * 101]:
            response = self.client.patch(self.url, {"documents": records}, format="json")
            self.assertEqual(response.status_code, 400, response.data)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.documents, [])
