"""Server-side manual GST selection regression checks."""
from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.suppliers.manual_gst import (
    manual_purchase_components, validate_manual_purchase_lines,
)


class ManualSupplierGstTests(SimpleTestCase):
    intra = {"taxMode": "cgst_sgst", "placeOfSupply": "32"}
    inter = {"taxMode": "igst", "placeOfSupply": "33"}
    ut = {"taxMode": "cgst_sgst", "placeOfSupply": "04"}

    def test_cgst_only_draft_is_fifty_paise_and_incomplete(self):
        result = manual_purchase_components(Decimal("20"), ["cgst:2.5"], self.intra)
        self.assertEqual(result["cgst"], Decimal("0.50"))
        self.assertEqual(result["sgst"], Decimal("0.00"))
        self.assertEqual(result["igst"], Decimal("0.00"))
        self.assertEqual(result["tax"], Decimal("0.50"))
        self.assertEqual(result["taxValidationStatus"], "incomplete")

    def test_matching_intra_pair_postable_without_automatic_selection(self):
        result = manual_purchase_components(
            Decimal("20"), ["cgst:2.5", "sgst:2.5"], self.intra
        )
        self.assertEqual(result["tax"], Decimal("1.00"))
        self.assertEqual(result["taxValidationStatus"], "valid")
        row = {**result, "subtotal": "20", "taxMode": "cgst_sgst", "placeOfSupply": "32",
               **{key: str(result[key]) for key in ("tax", "cgst", "sgst", "utgst", "igst")}}
        validate_manual_purchase_lines([row])

    def test_incorrect_or_incomplete_combinations_are_rejected_before_posting(self):
        for context, choices in (
            (self.intra, ["cgst:2.5"]),
            (self.intra, ["cgst:2.5", "sgst:9"]),
            (self.intra, ["cgst:5", "sgst:5"]),
            (self.inter, ["cgst:2.5", "sgst:2.5"]),
            (self.intra, ["igst:5"]),
            (self.ut, ["cgst:2.5", "sgst:2.5"]),
        ):
            with self.subTest(choices=choices):
                result = manual_purchase_components(Decimal("20"), choices, context)
                self.assertNotEqual(result["taxValidationStatus"], "valid")
                row = {**result, "subtotal": "20", **context,
                       **{key: str(result[key]) for key in ("tax", "cgst", "sgst", "utgst", "igst")}}
                with self.assertRaises(ValidationError):
                    validate_manual_purchase_lines([row])

    def test_utgst_and_igst_are_not_split_automatically(self):
        ut = manual_purchase_components(Decimal("100"), ["cgst:9", "utgst:9"], self.ut)
        self.assertEqual(ut["utgst"], Decimal("9.00"))
        self.assertEqual(ut["sgst"], Decimal("0.00"))
        self.assertEqual(ut["taxValidationStatus"], "valid")
        inter = manual_purchase_components(Decimal("100"), ["igst:18"], self.inter)
        self.assertEqual(inter["igst"], Decimal("18.00"))
        self.assertEqual(inter["cgst"], Decimal("0.00"))
        self.assertEqual(inter["taxValidationStatus"], "valid")

    def test_zero_requires_correct_classification_and_zero_rating_basis(self):
        unchecked = manual_purchase_components(Decimal("100"), ["zero"], self.intra)
        self.assertEqual(unchecked["taxValidationStatus"], "incomplete")
        valid = manual_purchase_components(Decimal("100"), ["zero"], self.intra, "exempt")
        self.assertEqual(valid["tax"], Decimal("0.00"))
        self.assertEqual(valid["taxValidationStatus"], "valid")
        unrated = manual_purchase_components(Decimal("100"), ["zero"], self.inter, "zero_rated")
        self.assertEqual(unrated["taxValidationStatus"], "incomplete")

    def test_duplicate_and_mixed_zero_selection_are_not_allowed(self):
        for choices in (["cgst:2.5", "cgst:2.5"], ["zero", "igst:5"]):
            with self.assertRaises(ValidationError):
                manual_purchase_components(Decimal("20"), choices, self.intra)
