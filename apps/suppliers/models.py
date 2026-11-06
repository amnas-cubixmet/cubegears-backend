"""Compatibility model API for suppliers.

Existing production models remain registered under inventory to preserve the
Django migration graph, foreign keys, table names and primary keys. This module
is the public supplier-domain import surface, not a second copy of the models.
Do not create duplicate tables or redefine these models here.
"""
from apps.inventory.models import (
    Supplier,
    SupplierBill,
    SupplierPayment,
    SupplierPaymentAllocation,
    PurchaseOrder,
    WorkshopFinancialAccount,
    WorkshopAccountEntry,
)

__all__ = [
    "Supplier", "SupplierBill", "SupplierPayment",
    "SupplierPaymentAllocation", "PurchaseOrder",
    "WorkshopFinancialAccount", "WorkshopAccountEntry",
]
