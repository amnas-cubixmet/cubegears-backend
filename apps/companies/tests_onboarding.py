"""Ensure organization setup persists to existing company/branch/settings data."""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.roles.models import Role
from apps.saas.models import CompanySetting


@override_settings(CREDIT_FEATURE_ENFORCEMENT_ENABLED=False,
                   CREDIT_LABEL_ONLY_ACCESS_ENABLED=False)
class SetupFlowTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Garage", slug="setup-garage")
        self.branch = Branch.objects.create(company=self.company, name="Head Office",
                                             code="HO", is_head_office=True)
        role = Role.objects.create(company=self.company, name="Owner", code="OWNER_SETUP",
                                   permissions=["company.manage"], is_active=True)
        self.owner = User.objects.create_user(email="setup@example.test", name="Owner",
                                               password="StrongPass123!", company=self.company,
                                               role=role, branch=self.branch)
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_complete_setup_saves_company_branch_gps_operations_billing(self):
        url = "/api/v1/company/onboarding"
        for step, data in (
            ("company", {"name": "New Garage", "state": "Kerala", "gstin": ""}),
            ("branch", {"name": "Main Workshop", "state": "Kerala",
                        "latitude": "11.200000", "longitude": "75.500000", "punchRadiusMeters": 120}),
            ("operations", {"workingStart": "09:00", "workingEnd": "18:00",
                            "bookingSlotMinutes": 30, "jobPrefix": "JOB-"}),
            ("billing", {"invoicePrefix": "CG-", "paymentTermsDays": 7,
                         "defaultTaxRate": "18.00", "rounding": True}),
        ):
            result = self.client.patch(url, {"step": step, "data": data}, format="json")
            self.assertEqual(result.status_code, 200, result.data)
        result = self.client.post(url + "/complete")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data["completed"])
        self.company.refresh_from_db()
        self.branch.refresh_from_db()
        self.assertEqual(self.company.name, "New Garage")
        self.assertEqual(self.branch.name, "Main Workshop")
        self.assertEqual(CompanySetting.objects.get(
            company=self.company, category="attendance",
        ).data["workshopLatitude"], "11.200000")
        self.assertEqual(CompanySetting.objects.get(
            company=self.company, category="billing",
        ).data["invoicePrefix"], "CG-")

    def test_cannot_finish_incomplete_setup(self):
        response = self.client.post("/api/v1/company/onboarding/complete")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(response.data["missingSteps"]), 4)

    def test_invalid_gps_does_not_mutate_branch(self):
        response = self.client.patch("/api/v1/company/onboarding", {
            "step": "branch", "data": {"name": "Invalid Name", "state": "Kerala",
                                     "latitude": "95", "longitude": "75"},
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.branch.refresh_from_db()
        self.assertEqual(self.branch.name, "Head Office")

    def test_old_workshop_is_not_forced_to_onboard(self):
        self.assertFalse(self.client.get("/api/v1/company/onboarding").data["required"])
