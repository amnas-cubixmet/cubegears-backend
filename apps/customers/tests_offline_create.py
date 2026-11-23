"""Offline customer creates must replay once, never duplicate or cross tenants."""
import uuid

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
class CustomerOfflineCreateTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Offline Workshop", slug="offline-customer-workshop")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN", is_active=True)
        self.other_branch = Branch.objects.create(
            company=self.company, name="Other", code="OTHER", is_active=True,
        )
        self.role = Role.objects.create(
            company=self.company, name="Customer Staff", code="OFFLINE_CUSTOMER_STAFF",
            permissions=["customers.view", "customers.create"], is_active=True,
        )
        self.owner = User.objects.create_user(
            company=self.company, role=self.role, branch=self.branch,
            email="offline-owner@example.test", password="test-password", name="Owner",
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def payload(self, key=None):
        return {
            "name": "Offline Customer",
            "phone": "+919876543210",
            "branch": str(self.branch.pk),
            "offlineRequestKey": key or str(uuid.uuid4()),
        }

    def test_replaying_same_key_returns_same_customer_without_duplicate(self):
        payload = self.payload()
        first = self.client.post("/api/v1/customers", payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        again = self.client.post("/api/v1/customers", payload, format="json")
        self.assertEqual(again.status_code, 200, again.data)
        self.assertEqual(first.data["id"], again.data["id"])
        self.assertEqual(Customer.objects.filter(company=self.company).count(), 1)
        self.assertNotIn("offline_payload_hash", first.data)
        self.assertNotIn("offline_request_key", first.data)

    def test_changed_payload_with_same_key_is_conflict(self):
        payload = self.payload()
        self.assertEqual(self.client.post("/api/v1/customers", payload, format="json").status_code, 201)
        changed = {**payload, "name": "Another Name"}
        rejected = self.client.post("/api/v1/customers", changed, format="json")
        self.assertEqual(rejected.status_code, 409, rejected.data)
        self.assertEqual(Customer.objects.count(), 1)

    def test_bad_key_and_unauthorized_branch_do_not_create(self):
        bad = self.client.post(
            "/api/v1/customers", self.payload("invalid"), format="json",
        )
        self.assertEqual(bad.status_code, 400, bad.data)
        staff = User.objects.create_user(
            company=self.company, role=self.role, branch=self.branch,
            email="offline-staff@example.test", password="test-password", name="Staff",
        )
        self.client.force_authenticate(user=staff)
        data = self.payload()
        data["branch"] = str(self.other_branch.pk)
        denied = self.client.post("/api/v1/customers", data, format="json")
        self.assertEqual(denied.status_code, 400, denied.data)
        self.assertFalse(Customer.objects.exists())

    def test_staff_cannot_replay_a_customer_in_another_branch(self):
        payload = self.payload()
        payload["branch"] = str(self.other_branch.pk)
        created = self.client.post("/api/v1/customers", payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        staff = User.objects.create_user(
            company=self.company, role=self.role, branch=self.branch,
            email="offline-private@example.test", password="test-password", name="Staff",
        )
        self.client.force_authenticate(user=staff)
        response = self.client.post("/api/v1/customers", payload, format="json")
        self.assertEqual(response.status_code, 403)
