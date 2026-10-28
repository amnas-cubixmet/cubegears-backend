"""Regression tests for the ten workshop GST dropdown entries."""
from decimal import Decimal

from django.test import SimpleTestCase

from .gst_rate_options import gst_rate_options, find_gst_choice


class WorkshopGstDropdownTests(SimpleTestCase):
    def test_labels_are_exactly_ten_distinct_options(self):
        options = gst_rate_options()
        self.assertEqual([choice["label"] for choice in options], [
            "CGST 2.5%", "CGST 5%", "CGST 9%", "CGST 18%",
            "SGST 2.5%", "SGST 5%", "SGST 9%", "SGST 18%",
            "IGST 5%", "IGST 18%",
        ])
        self.assertEqual(len({choice["value"] for choice in options}), 10)

    def test_component_selection_resolves_to_total_gst_rate(self):
        self.assertEqual(Decimal(find_gst_choice("cgst:9")["totalRate"]), Decimal("18"))
        self.assertEqual(Decimal(find_gst_choice("sgst:9")["totalRate"]), Decimal("18"))
        self.assertEqual(Decimal(find_gst_choice("igst:18")["totalRate"]), Decimal("18"))
        self.assertEqual(Decimal(find_gst_choice("igst:5")["totalRate"]), Decimal("5"))
        self.assertEqual(Decimal(find_gst_choice("cgst:18")["totalRate"]), Decimal("36"))
        self.assertEqual(find_gst_choice("igst:5")["taxMode"], "igst")
        self.assertEqual(find_gst_choice("sgst:9")["taxMode"], "cgst_sgst")
        self.assertIsNone(find_gst_choice("unknown"))
