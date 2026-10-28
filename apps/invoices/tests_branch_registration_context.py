"""GST-context diagnostics must reflect the exact branch/company state used for tax."""
from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.jobs.india_gst import gst_context, registration_state_diagnostics


class SupplyingBranchStateDiagnosticsTests(SimpleTestCase):
    def setUp(self):
        self.company = SimpleNamespace(gstin="32ABCDE1234F1Z5", state="Kerala")

    def test_matching_branch_state_keeps_gst_calculation_enabled(self):
        branch = SimpleNamespace(state="32-Kerala")
        details = registration_state_diagnostics(company=self.company, branch=branch)
        self.assertEqual(details["gstinStateCode"], "32")
        self.assertEqual(details["supplyingStateCode"], "32")
        self.assertEqual(details["stateSource"], "branch")
        self.assertFalse(details["registrationStateMismatch"])
        self.assertEqual(
            gst_context(company=self.company, branch=branch, customer=None,
                        config={"mode": "gst", "placeOfSupply": "32-Kerala"})["taxMode"],
            "cgst_sgst",
        )

    def test_different_branch_state_remains_blocked(self):
        branch = SimpleNamespace(state="Tamil Nadu")
        details = registration_state_diagnostics(company=self.company, branch=branch)
        self.assertEqual(details["supplyingStateCode"], "33")
        self.assertTrue(details["registrationStateMismatch"])
        with self.assertRaisesMessage(ValidationError, "Workshop branch state differs from company GSTIN"):
            gst_context(company=self.company, branch=branch, customer=None,
                        config={"mode": "gst", "placeOfSupply": "32-Kerala"})

    def test_without_branch_uses_company_state_and_flags_mismatch(self):
        self.company.state = "Tamil Nadu"
        details = registration_state_diagnostics(company=self.company, branch=None)
        self.assertEqual(details["stateSource"], "company")
        self.assertTrue(details["registrationStateMismatch"])

    def test_blank_branch_state_uses_company_fallback(self):
        branch = SimpleNamespace(state="")
        details = registration_state_diagnostics(company=self.company, branch=branch)
        self.assertEqual(details["stateSource"], "company")
        self.assertFalse(details["registrationStateMismatch"])

    def test_no_gstin_does_not_claim_mismatch(self):
        self.company.gstin = ""
        details = registration_state_diagnostics(
            company=self.company, branch=SimpleNamespace(state="Tamil Nadu"),
        )
        self.assertFalse(details["registrationStateMismatch"])
        self.assertEqual(details["gstinStateCode"], "")
