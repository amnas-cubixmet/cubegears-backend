"""Tax money regressions: backend is source of truth, never the browser totals."""
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.invoices.calculations import calculate_invoice_amounts, serialize_amounts


class InvoiceCalculationTests(SimpleTestCase):
    def setUp(self):
        self.company = SimpleNamespace(
            gstin="32ABCDE1234F1Z5", state="Kerala",
        )

    def calc(self, *, items=None, place="32-Kerala", discount=0, adjustment=0, paid=0):
        if items is None:
            items = [{"qty": "1", "rate": "100", "taxRate": "18", "discount": ""}]
        return calculate_invoice_amounts(
            company=self.company, invoice_type="gst",
            place_of_supply=place, items=items, discount=discount,
            adjustment=adjustment, paid=paid,
        )

    def test_100_plus_18_percent_is_118_and_qty_two_is_236(self):
        for quantity in (1, 2):
            result = self.calc(items=[{
                "qty": str(quantity), "rate": "100",
                "taxRate": "18", "discount": "0",
            }])
            self.assertEqual(result["taxable"], Decimal(100 * quantity))
            self.assertEqual(result["cgst"], Decimal(9 * quantity))
            self.assertEqual(result["sgst"], Decimal(9 * quantity))
            self.assertEqual(result["igst"], 0)
            self.assertEqual(result["tax"], Decimal(18 * quantity))
            self.assertEqual(result["lineTotals"][0]["total"], Decimal(118 * quantity))
            self.assertEqual(result["total"], Decimal(118 * quantity))

    def test_multiple_items_total_and_discount(self):
        result = self.calc(items=[
            {"qty": "2", "rate": "100", "taxRate": "18", "discount": "0"},
            {"qty": "1", "rate": "50", "taxRate": "10", "discount": "0"},
        ])
        self.assertEqual(result["taxable"], Decimal("250"))
        self.assertEqual(result["tax"], Decimal("41"))
        self.assertEqual(result["total"], Decimal("291"))
        self.assertEqual(len(result["lineTotals"]), 2)
        self.assertEqual(result["lineTotals"][0]["total"], Decimal("236"))
        self.assertEqual(result["lineTotals"][1]["total"], Decimal("55"))

    def test_interstate_igst_includes_tax(self):
        result = self.calc(place="33-Tamil Nadu")
        self.assertEqual(result["taxMode"], "igst")
        self.assertEqual(result["igst"], Decimal("18"))
        self.assertEqual(result["cgst"], 0)
        self.assertEqual(result["total"], Decimal("118"))

    def test_exempt_and_unregistered_no_gst(self):
        exempt = self.calc(items=[{"qty": "1", "rate": "100", "taxRate": "0"}])
        self.assertEqual(exempt["total"], Decimal("100"))
        unregistered = calculate_invoice_amounts(
            company=SimpleNamespace(gstin="", state="Kerala"),
            invoice_type="regular", items=[{"qty": "1", "rate": "100", "taxRate": "18"}],
        )
        self.assertEqual(unregistered["total"], Decimal("100"))
        self.assertEqual(unregistered["taxMode"], "none")

    def test_rounded_discount_received_and_balance(self):
        result = self.calc(discount="10", adjustment="0.50", paid="20")
        self.assertEqual(result["taxable"], Decimal("90"))
        self.assertEqual(result["cgst"], Decimal("8.10"))
        self.assertEqual(result["sgst"], Decimal("8.10"))
        self.assertEqual(result["total"], Decimal("106.70"))
        self.assertEqual(result["balance"], Decimal("86.70"))

    def test_invalid_numbers_and_missing_legal_place_rejected(self):
        with self.assertRaises(ValidationError):
            self.calc(items=[{"qty": "1", "rate": "abc", "taxRate": "18"}])
        with self.assertRaises(ValidationError):
            self.calc(items=[{"qty": "0", "rate": "100", "taxRate": "18"}])
        with self.assertRaises(ValidationError):
            self.calc(place="no state")

    def test_not_float_conversions(self):
        result = serialize_amounts(self.calc())
        self.assertEqual(result["total"], "118.00")
        self.assertEqual(result["cgst"], "9.00")
        self.assertEqual(result["lineTotals"][0]["total"], "118.00")
