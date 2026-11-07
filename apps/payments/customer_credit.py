"""Customer advance and credit ledger arithmetic.

Never treat an advance as revenue or re-post funds when applied to an invoice.
Invoice.paid includes both actual customer receipt allocations and advances
already applied to that invoice. Returns/refunds are separate cash movements.
"""
from decimal import Decimal

from django.db.models import Sum

from apps.invoices.models import Invoice
from .models import Payment, CustomerCreditAllocation


ZERO = Decimal("0.00")


def active_invoice_queryset(company, customer):
    return Invoice.objects.filter(
        company=company, customer=customer, kind="invoice",
        finalized_at__isnull=False,
    ).exclude(status__in=["Draft", "Cancelled"])


def paid_status(invoice):
    if invoice.balance <= ZERO:
        return "Paid"
    return "Partially Paid" if invoice.paid > ZERO else "Finalized"


def available_advance(advance):
    """Available credit for a posted, unapplied customer advance receipt."""
    if (advance.direction != "IN" or advance.invoice_id is not None
            or advance.customer_id is None or advance.status != "Completed"
            or advance.reversed_at is not None):
        return ZERO
    allocated = CustomerCreditAllocation.objects.filter(
        company_id=advance.company_id, advance_payment=advance,
        reversed_at__isnull=True,
    ).aggregate(value=Sum("amount"))["value"] or ZERO
    return max(ZERO, advance.amount - allocated)


def customer_credit_snapshot(company, customer, *, include_entries=True):
    """Single-tenant customer ledger. No bank balances or sensitive account data."""
    posted_invoices = active_invoice_queryset(company, customer)
    invoices = list(posted_invoices.order_by("-date", "-created_at"))
    receipts = list(Payment.objects.filter(
        company=company, customer=customer,
    ).order_by("-date", "-created_at"))
    advances = [
        payment for payment in receipts
        if payment.direction == "IN" and payment.invoice_id is None
        and payment.status == "Completed" and not payment.reversed_at
    ]
    allocations = list(CustomerCreditAllocation.objects.filter(
        company=company, advance_payment__customer=customer,
    ).select_related("invoice", "refund_payment", "advance_payment")
       .order_by("-created_at"))
    invoice_total = sum((invoice.total for invoice in invoices), ZERO)
    outstanding = sum((invoice.balance for invoice in invoices), ZERO)
    advance_received = sum((payment.amount for payment in advances), ZERO)
    advance_applied = sum((
        item.amount for item in allocations
        if item.invoice_id and not item.reversed_at
    ), ZERO)
    advance_refunded = sum((
        item.amount for item in allocations
        if item.refund_payment_id and not item.reversed_at
    ), ZERO)
    credit_available = max(ZERO, advance_received - advance_applied - advance_refunded)

    result = {
        "customerId": str(customer.pk),
        "currency": "INR",
        "totalInvoiced": str(invoice_total),
        "invoiceOutstanding": str(outstanding),
        "advanceReceived": str(advance_received),
        "advanceApplied": str(advance_applied),
        "advanceRefunded": str(advance_refunded),
        "advanceAvailable": str(credit_available),
        # Only a comparative metric; actual invoice balances change on APPLY.
        "netExposureAfterAvailableCredit": str(max(ZERO, outstanding-credit_available)),
        "advances": [{
            "id": str(advance.pk),
            "date": advance.date.isoformat(),
            "amount": str(advance.amount),
            "available": str(available_advance(advance)),
            "method": advance.method,
            "reference": advance.reference,
            "status": advance.status,
        } for advance in advances],
    }
    if include_entries:
        rows = []
        for invoice in invoices:
            rows.append({
                "id": str(invoice.pk), "type": "invoice",
                "date": invoice.date.isoformat(), "reference": invoice.number,
                "invoiceId": str(invoice.pk),
                "amount": str(invoice.total), "balance": str(invoice.balance),
                "status": invoice.status,
            })
        for payment in receipts:
            if payment.direction not in {"IN", "OUT"}:
                continue
            rows.append({
                "id": str(payment.pk),
                "type": ("advance" if payment.invoice_id is None else "receipt")
                    if payment.direction == "IN" else "refund",
                "date": payment.date.isoformat(),
                "reference": payment.reference or str(payment.pk),
                "invoiceId": str(payment.invoice_id) if payment.invoice_id else None,
                "amount": str(payment.amount),
                "method": payment.method,
                "status": payment.status,
            })
        for allocation in allocations:
            rows.append({
                "id": str(allocation.pk),
                "type": "credit_applied" if allocation.invoice_id else "credit_refunded",
                "date": allocation.created_at.date().isoformat(),
                "reference": allocation.invoice.number if allocation.invoice_id else
                    (allocation.refund_payment.reference or str(allocation.refund_payment_id)),
                "invoiceId": str(allocation.invoice_id) if allocation.invoice_id else None,
                "advanceId": str(allocation.advance_payment_id),
                "amount": str(allocation.amount),
                "status": "Reversed" if allocation.reversed_at else "Completed",
            })
        rows.sort(key=lambda row: (row["date"], row["id"]), reverse=True)
        result["entries"] = rows[:300]
    return result
