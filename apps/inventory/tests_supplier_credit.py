"""Regression checks for removal of an unfinished duplicate supplier-credit API.

The canonical supplier-bill, multi-allocation, payment, advance and cash/bank
workflows are covered by tests_supplier_finance.py. Legacy SupplierCredit
models were never migrated and must not be reintroduced.
"""
from django.apps import apps
from django.test import SimpleTestCase
from django.urls import resolve

from apps.inventory import supplier_finance_api


class SupplierCreditCompatibilityTests(SimpleTestCase):
    def test_canonical_procurement_routes_use_one_accounting_implementation(self):
        self.assertIs(
            resolve("/api/v1/stock/procurement/bills").func.view_class,
            supplier_finance_api.SupplierBillCollection,
        )
        self.assertIs(
            resolve("/api/v1/stock/procurement/payments").func.view_class,
            supplier_finance_api.SupplierPaymentCollection,
        )
        self.assertIs(
            resolve("/api/v1/stock/procurement/accounts").func.view_class,
            supplier_finance_api.AccountCollection,
        )

    def test_canonical_models_exist_for_posted_bills_and_allocated_payments(self):
        for name in (
            "SupplierBill", "SupplierPayment", "SupplierPaymentAllocation",
            "WorkshopFinancialAccount", "WorkshopAccountEntry",
        ):
            self.assertEqual(apps.get_model("inventory", name).__name__, name)

    def test_retired_duplicate_models_are_not_recreated(self):
        for name in ("SupplierCredit", "SupplierCreditPayment"):
            with self.assertRaises(LookupError):
                apps.get_model("inventory", name)
