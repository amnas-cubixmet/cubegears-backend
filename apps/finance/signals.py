"""Synchronous accounting projection: source transaction and GL commit together."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.expenses.models import Expense
from apps.invoices.models import Invoice
from apps.inventory.models import (
    SupplierBill, SupplierDebitApplication, SupplierDebitNote,
    SupplierPaymentAllocation, WorkshopAccountEntry, WorkshopFinancialAccount,
)

from .ledger_engine import (
    journal_bank_entry, journal_debit_application, journal_debit_note,
    journal_expense, journal_expense_delete, journal_invoice,
    journal_opening, journal_supplier_advance_allocation, journal_supplier_bill,
)


@receiver(post_save, sender=WorkshopFinancialAccount, dispatch_uid="part14_opening_journal")
def on_opening_account(sender, instance, raw=False, **kwargs):
    if not raw:
        journal_opening(instance)


@receiver(post_save, sender=WorkshopAccountEntry, dispatch_uid="part14_cash_journal")
def on_cash_entry(sender, instance, raw=False, **kwargs):
    if not raw:
        journal_bank_entry(instance)


@receiver(post_save, sender=SupplierBill, dispatch_uid="part14_supplier_bill_journal")
def on_supplier_bill(sender, instance, raw=False, **kwargs):
    if not raw and instance.status == "Posted":
        journal_supplier_bill(instance)


@receiver(post_save, sender=SupplierDebitNote, dispatch_uid="part14_supplier_debit_journal")
def on_supplier_debit(sender, instance, raw=False, **kwargs):
    if not raw:
        journal_debit_note(instance)


@receiver(post_save, sender=SupplierDebitApplication, dispatch_uid="part14_supplier_debit_application_journal")
def on_supplier_debit_apply(sender, instance, raw=False, **kwargs):
    if not raw:
        journal_debit_application(instance)


@receiver(post_save, sender=SupplierPaymentAllocation, dispatch_uid="part14_supplier_advance_application_journal")
def on_advance_apply(sender, instance, raw=False, **kwargs):
    if not raw and instance.payment.kind == "Advance":
        journal_supplier_advance_allocation(instance)


@receiver(post_save, sender=Invoice, dispatch_uid="part14_invoice_journal")
def on_invoice(sender, instance, raw=False, **kwargs):
    if not raw and instance.kind == "invoice" and instance.finalized_at:
        journal_invoice(instance)


@receiver(post_save, sender=Expense, dispatch_uid="part14_expense_journal")
def on_expense(sender, instance, raw=False, **kwargs):
    if not raw:
        journal_expense(instance)


@receiver(post_delete, sender=Expense, dispatch_uid="part14_expense_delete_journal")
def on_expense_delete(sender, instance, **kwargs):
    journal_expense_delete(instance)
