"""Ensure organization setup persists to existing company/branch/settings data."""

from django.test import TestCase, override_settings
from decimal import Decimal
from apps.inventory.models import WorkshopFinancialAccount
from apps.finance.models import JournalEntry
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
            ("bank", {"mode": "skip"}),
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
        from apps.attendance.services import get_attendance_rule
        attendance_rule = get_attendance_rule(self.company, self.branch)
        self.assertEqual(attendance_rule.punch_location_mode, "workshop")
        self.assertTrue(attendance_rule.location_required)
        self.assertEqual(attendance_rule.workshop_latitude, 11.2)
        self.assertEqual(attendance_rule.workshop_longitude, 75.5)
        self.assertEqual(attendance_rule.punch_radius_meters, 120)
        self.assertEqual(CompanySetting.objects.get(
            company=self.company, category="billing",
        ).data["invoicePrefix"], "CG-")

    def test_cannot_finish_incomplete_setup(self):
        response = self.client.post("/api/v1/company/onboarding/complete")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(response.data["missingSteps"]), 5)

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

    def test_bank_setup_creates_one_real_finance_account_and_posts_opening_once(self):
        payload = {
            "mode": "new",
            "name": "Primary SBI Bank",
            "bankName": "State Bank of India",
            "accountNumber": "123456789012",
            "ifsc": "SBIN0001234",
            "branchName": "Main Town",
            "openingBalance": "4500.50",
            "openingDate": "2026-11-09",
        }
        url = "/api/v1/company/onboarding"
        response = self.client.patch(url, {"step": "bank", "data": payload}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["bank"]["configured"])
        self.assertEqual(response.data["bank"]["name"], "Primary SBI Bank")
        self.assertEqual(response.data["bank"]["accountNumberMasked"], "••••9012")
        self.assertNotIn("123456789012", str(response.data))
        self.assertEqual(len(WorkshopFinancialAccount.objects.filter(
            company=self.company, kind="Bank",
        )), 1)
        bank = WorkshopFinancialAccount.objects.get(company=self.company, kind="Bank")
        self.assertEqual(bank.opening_balance, Decimal("4500.50"))
        self.assertEqual(JournalEntry.objects.filter(
            company=self.company, source_key=f"opening:{bank.pk}",
        ).count(), 1)
        first = JournalEntry.objects.get(company=self.company, source_key=f"opening:{bank.pk}")
        self.assertEqual(first.lines.count(), 2)
        self.assertEqual(sum(row.debit for row in first.lines.all()), Decimal("4500.50"))
        self.assertEqual(sum(row.credit for row in first.lines.all()), Decimal("4500.50"))

        # A double click/retry does not create a second account or opening journal.
        again = self.client.patch(url, {"step": "bank", "data": payload}, format="json")
        self.assertEqual(again.status_code, 200, again.data)
        self.assertEqual(WorkshopFinancialAccount.objects.filter(
            company=self.company, kind="Bank",
        ).count(), 1)
        self.assertEqual(JournalEntry.objects.filter(
            company=self.company, source_key=f"opening:{bank.pk}",
        ).count(), 1)
        # Existing opening entries are immutable; use the finance ledger for changes.
        changed = self.client.patch(url, {"step": "bank", "data": {
            **payload, "openingBalance": "99000.00",
        }}, format="json")
        self.assertEqual(changed.status_code, 400)
        bank.refresh_from_db()
        self.assertEqual(bank.opening_balance, Decimal("4500.50"))

    def test_bank_can_be_skipped_without_creating_a_financial_account(self):
        url = "/api/v1/company/onboarding"
        response = self.client.patch(url, {
            "step": "bank", "data": {"mode": "skip"},
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["bank"]["skipped"])
        self.assertIn("bank", response.data["completedSteps"])
        self.assertEqual(WorkshopFinancialAccount.objects.filter(
            company=self.company,
        ).count(), 0)
        self.assertEqual(JournalEntry.objects.filter(company=self.company).count(), 0)

    def test_bank_can_link_existing_same_tenant_account_not_foreign(self):
        local = WorkshopFinancialAccount.objects.create(
            company=self.company, name="Old Bank", kind="Bank",
            opening_balance=Decimal("200.00"),
        )
        foreign_company = Company.objects.create(name="Other Garage", slug="other-garage-bank")
        foreign = WorkshopFinancialAccount.objects.create(
            company=foreign_company, name="Foreign Bank", kind="Bank",
        )
        url = "/api/v1/company/onboarding"
        forbidden = self.client.patch(url, {
            "step": "bank", "data": {"mode": "existing", "accountId": str(foreign.pk)},
        }, format="json")
        self.assertEqual(forbidden.status_code, 400)
        linked = self.client.patch(url, {
            "step": "bank", "data": {"mode": "existing", "accountId": str(local.pk)},
        }, format="json")
        self.assertEqual(linked.status_code, 200, linked.data)
        self.assertEqual(linked.data["bank"]["accountId"], str(local.pk))
        self.assertEqual(WorkshopFinancialAccount.objects.filter(
            company=self.company, kind="Bank",
        ).count(), 1)
        self.assertFalse(linked.data["bank"]["skipped"])

    def test_existing_four_step_completed_workshop_is_not_relocked_for_bank(self):
        CompanySetting.objects.create(
            company=self.company, branch=self.branch, category="onboarding",
            data={"required": True, "completed": True,
                  "completedSteps": ["company", "branch", "operations", "billing"]},
        )
        response = self.client.get("/api/v1/company/onboarding")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["completed"])
        self.assertIn("bank", response.data["completedSteps"])

    def test_updating_onboarding_location_updates_punch_policy_immediately(self):
        from apps.attendance.services import get_attendance_rule
        from apps.attendance.punch_location import validate_punch_location

        url = "/api/v1/company/onboarding"
        first = self.client.patch(url, {"step": "branch", "data": {
            "name": "Head Office", "state": "Kerala",
            "latitude": "11.250000", "longitude": "75.800000",
            "punchRadiusMeters": 50,
        }}, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        rule = get_attendance_rule(self.company)
        self.assertEqual(rule.punch_location_mode, "workshop")
        self.assertEqual(rule.punch_radius_meters, 50)
        self.assertEqual(validate_punch_location(rule, {
            "latitude": 11.2501, "longitude": 75.8001, "accuracy": 10,
        })["accuracy"], 10)
        with self.assertRaisesRegex(ValueError, "Outside the permitted"):
            validate_punch_location(rule, {
                "latitude": 11.29, "longitude": 75.8, "accuracy": 10,
            })

        changed = self.client.patch(url, {"step": "branch", "data": {
            "name": "Head Office", "state": "Kerala",
            "latitude": "12.000000", "longitude": "76.000000",
            "punchRadiusMeters": 200,
        }}, format="json")
        self.assertEqual(changed.status_code, 200, changed.data)
        rule = get_attendance_rule(self.company)
        self.assertEqual(rule.workshop_latitude, 12.0)
        self.assertEqual(rule.workshop_longitude, 76.0)
        self.assertEqual(rule.punch_radius_meters, 200)

        cleared = self.client.patch(url, {"step": "branch", "data": {
            "name": "Head Office", "state": "Kerala",
            "latitude": None, "longitude": None,
        }}, format="json")
        self.assertEqual(cleared.status_code, 200, cleared.data)
        rule = get_attendance_rule(self.company)
        self.assertEqual(rule.punch_location_mode, "anywhere")
        self.assertFalse(rule.location_required)
