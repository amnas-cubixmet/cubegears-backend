from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from .services import parse_period
from .views import _official_2b_rows, _reconcile


class PeriodTests(SimpleTestCase):
    def test_financial_year_april_transition(self):
        self.assertEqual(parse_period("2026-04")["financialYear"], "2026-27")
        self.assertEqual(parse_period("2027-03")["financialYear"], "2026-27")

    def test_invalid_period_fails_closed(self):
        for value in ("2026-13", "2026-00", "2026-1", "2040-04' OR 1=1"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    parse_period(value)


class Official2BParsingTests(SimpleTestCase):
    def test_matching_gstin_period_and_b2b_invoice(self):
        payload = {
            "gstin": "32ABCDE1234F1Z5", "rtnprd": "102026",
            "docdata": {"b2b": [{
                "ctin": "32ABCDE6789G1Z5",
                "inv": [{"inum": "TEST-21", "idt": "14-10-2026",
                         "itms": [{"itm_det": {"txval": 100, "iamt": 0,
                                                "camt": 9, "samt": 9}}]}],
            }]},
        }
        rows, unsupported = _official_2b_rows(
            payload, "32ABCDE1234F1Z5", "2026-10")
        self.assertEqual(unsupported, [])
        self.assertEqual(rows[0]["taxable"], "100.00")
        self.assertEqual(rows[0]["cgst"], "9.00")

    def test_wrong_tenant_gstin_rejected(self):
        payload = {"gstin": "32ABCDE1234F1Z5", "rtnprd": "102026",
                   "docdata": {"b2b": []}}
        with self.assertRaises(ValidationError):
            _official_2b_rows(payload, "32ABCDE6789G1Z5", "2026-10")

    def test_unsupported_sections_are_explicit(self):
        payload = {"gstin": "32ABCDE1234F1Z5", "rtnprd": "102026",
                   "docdata": {"b2b": [], "cdnr": [{"doc": "not interpreted"}]}}
        rows, unsupported = _official_2b_rows(
            payload, "32ABCDE1234F1Z5", "2026-10")
        self.assertEqual(rows, [])
        self.assertEqual(unsupported, ["cdnr"])


class ReconciliationTests(SimpleTestCase):
    def setUp(self):
        self.book = {
            "id": "bill-1", "supplierGSTIN": "32ABCDE6789G1Z5",
            "number": "TEST-21", "date": "2026-10-14",
            "taxable": "100.00", "cgst": "9.00",
            "sgst": "9.00", "igst": "0.00", "tax": "18.00",
            "taxVerified": True,
        }
        self.portal = {
            "supplierGSTIN": "32ABCDE6789G1Z5",
            "number": "TEST-21", "date": "14-10-2026",
            "taxable": "100.00", "cgst": "9.00",
            "sgst": "9.00", "igst": "0.00",
        }

    def test_exact_match(self):
        self.assertEqual(_reconcile([self.book], [self.portal])[0]["status"], "Matched")

    def test_tax_head_mismatch(self):
        portal = {**self.portal, "cgst": "8.00", "sgst": "10.00"}
        self.assertEqual(_reconcile([self.book], [portal])[0]["status"], "Tax Mismatch")

    def test_date_mismatch_requires_review(self):
        portal = {**self.portal, "date": "15-10-2026"}
        self.assertEqual(_reconcile([self.book], [portal])[0]["status"], "Requires Review")

    def test_missing_in_books_and_in_statement(self):
        portal = {**self.portal, "number": "OTHER"}
        statuses = [r["status"] for r in _reconcile([self.book], [portal])]
        self.assertEqual(statuses, ["Missing in Books", "Missing in GSTR-2B"])

    def test_duplicate_portal_invoice_detected(self):
        statuses = [r["status"] for r in _reconcile(
            [self.book], [self.portal, self.portal])]
        self.assertEqual(statuses, ["Duplicate", "Duplicate"])
