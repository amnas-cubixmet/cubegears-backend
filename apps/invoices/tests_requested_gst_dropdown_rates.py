"""GST UI presets and authoritative Decimal totals for company invoices/estimates."""
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.jobs.india_gst import gst_dropdown_presets
from apps.invoices.calculations import calculate_invoice_amounts


class RequestedGstRateTests(SimpleTestCase):
    def setUp(self):
        self.company = SimpleNamespace(gstin="32ABCDE1234F1Z5", state="Kerala")

    def calc(self, rate, destination):
        return calculate_invoice_amounts(
            company=self.company, invoice_type="gst",
            place_of_supply=destination,
            items=[{"qty": "1", "rate": "100", "discount": "0", "taxRate": rate}],
        )

    def test_dropdown_metadata_is_cgst_sgst_four_pairs_and_unique_igst(self):
        self.assertEqual(gst_dropdown_presets(), {
            "cgst": ["2.5", "5", "9", "18"],
            "sgst": ["2.5", "5", "9", "18"],
            "igst": ["5"],
            "intraTotal": ["5", "10", "18", "36"],
        })

    def test_each_intra_state_pair_is_split_evenly_by_backend(self):
        for total, component in [("5", "2.50"), ("10", "5.00"),
                                 ("18", "9.00"), ("36", "18.00")]:
            with self.subTest(total=total):
                amount = self.calc(total, "32-Kerala")
                self.assertEqual(amount["taxMode"], "cgst_sgst")
                self.assertEqual(amount["cgst"], Decimal(component))
                self.assertEqual(amount["sgst"], Decimal(component))
                self.assertEqual(amount["igst"], 0)
                self.assertEqual(amount["total"], Decimal("100") + Decimal(total))

    def test_interstate_igst_five_is_not_split(self):
        amount = self.calc("5", "33-Tamil Nadu")
        self.assertEqual(amount["taxMode"], "igst")
        self.assertEqual(amount["cgst"], 0)
        self.assertEqual(amount["sgst"], 0)
        self.assertEqual(amount["igst"], Decimal("5.00"))
        self.assertEqual(amount["total"], Decimal("105.00"))

    def test_existing_18_percent_interstate_saved_data_still_calculates(self):
        amount = self.calc("18", "33-Tamil Nadu")
        self.assertEqual(amount["igst"], Decimal("18.00"))
        self.assertEqual(amount["total"], Decimal("118.00"))
