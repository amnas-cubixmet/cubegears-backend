"""Idempotent, balanced journal adapter for existing financial source records.

The existing WorkshopAccountEntry cash ledger remains authoritative for account
balances. These immutable debit/credit entries classify the same transactions.
Historical source rows are imported only through the explicit audit command.
"""
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import JournalEntry, JournalLine, LedgerAccount

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
CHART = {
    "1100": ("Trade Receivables", "Asset"),
    "1150": ("Supplier Refund Receivables", "Asset"),
    "1200": ("Inventory / Purchased Parts", "Asset"),
    "1250": ("Supplier Advances", "Asset"),
    "2000": ("Trade Payables — Suppliers", "Liability"),
    "2050": ("Operating Expense Payables", "Liability"),
    "2100": ("Customer Advances", "Liability"),
    "2150": ("Output GST Payable", "Liability"),
    "2190": ("Internal Transfer Clearing", "Liability"),
    "3000": ("Opening Capital / Equity Adjustments", "Equity"),
    "4000": ("Workshop Sales Revenue", "Income"),
    "5000": ("Purchase & Service Costs", "Expense"),
    "5100": ("Operating Expenses", "Expense"),
    "9999": ("Unclassified / Historical Suspense", "Equity"),
}


def account_for(company_id, code, financial_account=None):
    if financial_account is not None:
        if financial_account.company_id != company_id:
            raise ValidationError({"account": "Financial account belongs to another workshop."})
        code = "CASH:" + str(financial_account.pk)
        name, kind = financial_account.name, "Asset"
    else:
        if code not in CHART:
            raise ValidationError({"code": "Unknown accounting code."})
        name, kind = CHART[code]
    account, _ = LedgerAccount.objects.get_or_create(
        company_id=company_id, code=code,
        defaults={
            "name": name, "account_type": kind,
            "financial_account": financial_account,
        },
    )
    if account.account_type != kind or account.financial_account_id != (
        financial_account.pk if financial_account else None
    ):
        raise ValidationError({"account": "Chart of accounts mapping mismatch."})
    return account


@transaction.atomic
def post_journal(*, company_id, source_key, memo, lines, date=None, created_by_id=None,
                 entry_kind="Automatic", reference=""):
    """One unique source -> 2+ immutable lines; reject any unbalanced posting."""
    if not source_key or len(source_key) > 180:
        raise ValidationError({"sourceKey": "Invalid journal source key."})
    existing = JournalEntry.objects.filter(
        company_id=company_id, source_key=source_key,
    ).first()
    if existing:
        return existing, False
    if len(lines) < 2:
        raise ValidationError({"lines": "A journal needs at least two accounts."})
    debits = credits = ZERO
    parsed = []
    for row in lines:
        account = row["account"]
        if account.company_id != company_id or not account.is_active:
            raise ValidationError({"lines": "Journal uses an inactive or foreign account."})
        debit = Decimal(str(row.get("debit", ZERO)))
        credit = Decimal(str(row.get("credit", ZERO)))
        if (not debit.is_finite() or not credit.is_finite()
                or debit < ZERO or credit < ZERO
                or debit.as_tuple().exponent < -2 or credit.as_tuple().exponent < -2
                or bool(debit) == bool(credit)):
            raise ValidationError({"lines": "Each journal line must have one positive debit or credit."})
        debits += debit
        credits += credit
        parsed.append((account, debit, credit))
    if debits != credits or debits <= ZERO:
        raise ValidationError({"lines": "Total debits must exactly equal total credits."})
    journal = JournalEntry.objects.create(
        company_id=company_id, source_key=source_key,
        reference=str(reference or "")[:160],
        memo=str(memo or source_key)[:500],
        posted_at=date or timezone.now(), created_by_id=created_by_id,
        entry_kind=entry_kind,
    )
    JournalLine.objects.bulk_create([
        JournalLine(journal=journal, account=a, debit=d, credit=c)
        for a, d, c in parsed
    ])
    return journal, True


def pair(company_id, source_key, memo, debit_code, credit_code, amount, *,
         debit_account=None, credit_account=None, date=None,
         created_by_id=None, reference="", entry_kind="Automatic"):
    amount = Decimal(str(amount))
    if amount <= ZERO or not amount.is_finite():
        return None
    debit = account_for(company_id, debit_code, debit_account)
    credit = account_for(company_id, credit_code, credit_account)
    if debit.pk == credit.pk:
        raise ValidationError({"journal": "Debit and credit accounts must be different."})
    return post_journal(
        company_id=company_id, source_key=source_key, memo=memo,
        lines=[
            {"account": debit, "debit": amount},
            {"account": credit, "credit": amount},
        ],
        date=date, created_by_id=created_by_id, reference=reference,
        entry_kind=entry_kind,
    )


def journal_opening(account):
    if not account.opening_balance:
        return None
    if account.opening_balance > ZERO:
        debit, credit = "CASH", "3000"
    else:
        debit, credit = "3000", "CASH"
    return pair(
        account.company_id, f"opening:{account.pk}",
        "Recorded opening balance: " + account.name,
        debit, credit, abs(account.opening_balance),
        debit_account=account if debit == "CASH" else None,
        credit_account=account if credit == "CASH" else None,
        date=timezone.make_aware(
            __import__("datetime").datetime.combine(
                account.opening_date or timezone.localdate(),
                __import__("datetime").time.min,
            )
        ), created_by_id=account.created_by_id,
        reference=account.name,
    )


def journal_bank_entry(entry):
    """Cash entry always gets one opposite GL line. Never mutate cash balance."""
    from apps.inventory.models import SupplierRefund
    from apps.payments.models import Payment
    entry_type = str(entry.entry_type or "").lower().strip()
    source_key = str(entry.source_key or "")
    offset = "9999"  # Explicitly unclassified; do not invent income/profit.
    if entry_type in {"transfer_in", "transfer_out"}:
        offset = "2190"
    elif entry_type == "supplier payment":
        offset = "2000"
    elif entry_type == "supplier advance":
        offset = "1250"
    elif entry_type == "supplier refund":
        ref_id = source_key.removeprefix("supplier-refund:")
        refund = SupplierRefund.objects.filter(
            company_id=entry.company_id, pk=ref_id,
        ).first() if source_key.startswith("supplier-refund:") else None
        offset = "1250" if refund and refund.advance_id else "1150"
    elif entry_type in {"expense_payment", "expense_reversal"}:
        offset = "2050"
    elif entry_type in {"customer_payment", "payment_reversal", "outgoing_payment"}:
        payment_id = source_key.removeprefix("customer_payment_reverse_").removeprefix("customer_payment_")
        payment = Payment.objects.filter(
            pk=payment_id, company_id=entry.company_id,
        ).first() if payment_id != source_key else None
        offset = "1100" if payment and payment.invoice_id else "2100"
    elif entry_type in {"manual_adjustment", "manual_equity"}:
        offset = "3000"
    elif entry_type == "manual_income":
        offset = "4000"
    elif entry_type == "manual_expense":
        offset = "5100"
    cash_is_debit = entry.amount > ZERO
    return pair(
        entry.company_id, "cash:" + str(entry.pk),
        entry.entry_type + " — " + entry.account.name,
        "CASH" if cash_is_debit else offset,
        offset if cash_is_debit else "CASH",
        abs(entry.amount),
        debit_account=entry.account if cash_is_debit else None,
        credit_account=entry.account if not cash_is_debit else None,
        date=entry.posted_at, created_by_id=entry.created_by_id,
        reference=entry.reference,
    )


def journal_supplier_bill(bill):
    if bill.status != "Posted" or bill.total <= ZERO:
        return None
    return pair(
        bill.company_id, f"supplier-bill:{bill.pk}",
        "Supplier invoice " + bill.invoice_number,
        "1200" if bill.receive_into_stock else "5000", "2000",
        bill.total, date=bill.posted_at or timezone.now(),
        created_by_id=bill.created_by_id, reference=bill.invoice_number,
    )


def journal_debit_note(note):
    if note.total <= ZERO:
        return None
    # The same return may both cancel an unpaid payable and create a claim
    # against funds previously paid: debit both, credit inventory/cost once.
    source = "supplier-debit:" + str(note.pk)
    lines = []
    if note.payable_reduction:
        lines.append({
            "account": account_for(note.company_id, "2000"),
            "debit": note.payable_reduction,
        })
    if note.credit_amount:
        lines.append({
            "account": account_for(note.company_id, "1150"),
            "debit": note.credit_amount,
        })
    lines.append({
        "account": account_for(
            note.company_id, "1200" if note.bill.receive_into_stock else "5000",
        ),
        "credit": note.total,
    })
    return post_journal(
        company_id=note.company_id, source_key=source,
        memo="Debit note " + note.number,
        lines=lines, created_by_id=note.created_by_id, reference=note.number,
        date=timezone.now(),
    )


def journal_debit_application(application):
    return pair(
        application.company_id, f"supplier-debit-apply:{application.pk}",
        "Supplier debit credit applied to bill " + application.bill.invoice_number,
        "2000", "1150", application.amount,
        date=application.created_at or timezone.now(),
        created_by_id=application.created_by_id, reference=application.bill.invoice_number,
    )


def journal_supplier_advance_allocation(allocation):
    if allocation.payment.kind != "Advance":
        return None
    return pair(
        allocation.company_id, f"supplier-advance-apply:{allocation.pk}",
        "Supplier advance applied to bill " + allocation.bill.invoice_number,
        "2000", "1250", allocation.amount,
        date=allocation.created_at or timezone.now(),
        reference=allocation.bill.invoice_number,
        created_by_id=allocation.payment.created_by_id,
    )


def journal_invoice(invoice):
    if invoice.kind != "invoice" or not invoice.finalized_at or invoice.total <= ZERO:
        return None
    gross = Decimal(invoice.total)
    gst = min(gross, Decimal(invoice.cgst) + Decimal(invoice.sgst) + Decimal(invoice.igst))
    net = gross - gst
    lines = [{"account": account_for(invoice.company_id, "1100"), "debit": gross}]
    if net:
        lines.append({"account": account_for(invoice.company_id, "4000"), "credit": net})
    if gst:
        lines.append({"account": account_for(invoice.company_id, "2150"), "credit": gst})
    if invoice.status == "Cancelled":
        return journal_invoice_cancelled(invoice)
    return post_journal(
        company_id=invoice.company_id, source_key=f"invoice:{invoice.pk}",
        memo="Finalized workshop invoice " + invoice.number,
        lines=lines, date=invoice.finalized_at,
        reference=invoice.number,
    )


def journal_invoice_cancelled(invoice):
    original = JournalEntry.objects.filter(
        company_id=invoice.company_id, source_key=f"invoice:{invoice.pk}",
    ).prefetch_related("lines").first()
    if not original:
        return None
    return post_journal(
        company_id=invoice.company_id, source_key=f"invoice-cancel:{invoice.pk}",
        memo="Invoice cancellation " + invoice.number,
        lines=[
            {"account": line.account, "debit": line.credit, "credit": line.debit}
            for line in original.lines.all()
        ],
        date=invoice.cancelled_at or timezone.now(),
        reference=invoice.number,
    )


def journal_expense(expense):
    """Accrue operating expense; revising unpaid amounts creates a signed correction."""
    if expense.amount <= ZERO:
        return None
    account_expense = account_for(expense.company_id, "5100")
    existing = JournalEntry.objects.filter(
        company_id=expense.company_id, source_key__startswith=f"expense:{expense.pk}:",
    )
    already = ZERO
    for row in JournalLine.objects.filter(journal__in=existing, account=account_expense):
        already += row.debit - row.credit
    delta = Decimal(expense.amount) - already
    if not delta:
        return None
    direction = delta > ZERO
    version = f"expense:{expense.pk}:{expense.updated_at.isoformat()}"
    return pair(
        expense.company_id, version,
        "Expense accrual or correction " + str(expense.pk),
        "5100" if direction else "2050",
        "2050" if direction else "5100",
        abs(delta), date=timezone.now(),
        created_by_id=expense.created_by_id,
        reference=str(expense.pk),
    )


def journal_expense_delete(expense):
    """Reverse previously recognized liability for an unpaid deleted expense."""
    existing = JournalEntry.objects.filter(
        company_id=expense.company_id, source_key__startswith=f"expense:{expense.pk}:",
    )
    account_expense = account_for(expense.company_id, "5100")
    accrued = ZERO
    for line in JournalLine.objects.filter(journal__in=existing, account=account_expense):
        accrued += line.debit - line.credit
    if accrued <= ZERO:
        return None
    return pair(
        expense.company_id, f"expense-delete:{expense.pk}",
        "Removed unpaid expense " + str(expense.pk),
        "2050", "5100", accrued, reference=str(expense.pk),
        date=timezone.now(), created_by_id=expense.created_by_id,
    )
