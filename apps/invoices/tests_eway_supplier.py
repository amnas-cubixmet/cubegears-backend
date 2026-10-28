"""Protect outward E-Way Bill FROM against forged payloads, and split Indian GST."""
from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.invoices.eway_validation import workshop_supply_party, validate_tax_geography
from apps.invoices.serializers import EWayBillSerializer


class EWayBillSupplierAndTaxTests(SimpleTestCase):
    def setUp(self):
        self.company = SimpleNamespace(
            name="Demo Garage", gstin="32ABCDE1234F1Z5",
            address="Main Road", city="Thrissur", pincode="680001", state="Kerala",
        )
        self.branch = SimpleNamespace(
            name="Service Branch", address="Workshop Street", city="Kochi",
            pincode="682001", state="32-Kerala",
        )

    def test_from_uses_actual_branch_contact_not_sample_kerala_pincode(self):
        party = workshop_supply_party(self.company, self.branch)
        self.assertEqual(party, {
            "gstin": "32ABCDE1234F1Z5",
            "tradeName": "Demo Garage",
            "address": "Workshop Street", "place": "Kochi",
            "pincode": "682001", "stateCode": "32",
        })

    def test_company_fallback_and_no_unverified_state_default(self):
        self.assertEqual(workshop_supply_party(self.company, None)["pincode"], "680001")
        self.company.state = ""
        self.assertEqual(workshop_supply_party(self.company, None)["stateCode"], "")

    def test_client_cannot_spoof_outward_supplier_in_serializer(self):
        request = SimpleNamespace(user=SimpleNamespace(company=self.company, branch=self.branch))
        serializer = EWayBillSerializer(context={"request": request})
        data = serializer.to_internal_value({
            "supplyType": "Outward",
            "from": {"gstin": "33FAKE1234567890", "tradeName": "Fraud",
                     "address": "Other", "stateCode": "33"},
            "to": {"tradeName": "Buyer"}, "transport": {"mode": "Road"},
        })
        self.assertEqual(data["payload"]["from"]["gstin"], self.company.gstin)
        self.assertEqual(data["payload"]["from"]["place"], "Kochi")

    def test_inward_sender_is_not_overwritten_with_workshop(self):
        request = SimpleNamespace(user=SimpleNamespace(company=self.company, branch=self.branch))
        serializer = EWayBillSerializer(context={"request": request})
        data = serializer.to_internal_value({
            "supplyType": "Inward",
            "from": {"gstin": "URP", "tradeName": "Actual Vendor"},
            "to": {"tradeName": "Demo Garage"},
        })
        self.assertEqual(data["payload"]["from"]["tradeName"], "Actual Vendor")

    def test_intrastate_and_interstate_gst_both_valid(self):
        base = {"subSupplyType": "Supply", "from": {"stateCode": "32"}}
        validate_tax_geography({**base, "to": {"stateCode": "32"},
            "items": [{"cgstRate": 9, "sgstRate": 9, "igstRate": 0}]})
        validate_tax_geography({**base, "to": {"stateCode": "33"},
            "items": [{"cgstRate": 0, "sgstRate": 0, "igstRate": 18}]})

    def test_conflicting_cgst_igst_blocked_for_normal_supply(self):
        base = {"subSupplyType": "Supply", "from": {"stateCode": "32"}}
        with self.assertRaisesMessage(ValidationError, "intra-state"):
            validate_tax_geography({**base, "to": {"stateCode": "32"},
                "items": [{"igstRate": 18}]})
        with self.assertRaisesMessage(ValidationError, "inter-state"):
            validate_tax_geography({**base, "to": {"stateCode": "33"},
                "items": [{"cgstRate": 9, "sgstRate": 9}]})
