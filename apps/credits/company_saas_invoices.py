"""Read-only, company-scoped SaaS credit billing documents.

Workshop sales invoices are intentionally excluded. Existing SaaS credit
statements are persisted by one of three independent billing engines.
They are denominated in wallet credits, NOT INR/GST tax invoices.
"""
from django.utils import timezone
from django.shortcuts import get_object_or_404

from .models import (
    BillingCycleStatement, CreditCycleInvoice, MonthlyUsageInvoice,
)

DOCUMENT_MODELS = {
    "calendar": BillingCycleStatement,
    "monthly": MonthlyUsageInvoice,
    "cycle": CreditCycleInvoice,
}


def _normalize(source, item, *, now):
    if source == "calendar":
        category = "Features + storage"
        period = item.month
        total = item.total_credits
        feature = item.feature_credits
        storage = item.storage_credits
        issued_at = item.finalized_at
        due_at = item.due_at
        paid_at = item.settled_at
        details = item.details
    elif source == "monthly":
        category = item.get_category_display()
        period = item.billing_month
        total = item.credits_due
        feature = total if item.category == "feature" else None
        storage = total if item.category == "storage" else None
        issued_at = item.created_at
        due_at = None  # Legacy model has no due_at; don't infer one.
        paid_at = item.settled_at
        details = item.details
    else:
        category = item.get_category_display()
        period = item.starts_on.strftime("%Y-%m-%d")
        total = item.credits_due
        feature = total if item.category == "feature" else None
        storage = total if item.category == "storage" else None
        issued_at = item.created_at
        due_at = item.due_at
        paid_at = item.settled_at
        details = item.details
    overdue = bool(due_at and item.status != "paid" and due_at < now)
    return {
        "source": source,
        "pk": item.pk,
        "reference": f"CG-{source.upper()}-{item.pk:06d}",
        "category": category,
        "period": period,
        "issued_at": issued_at,
        "due_at": due_at,
        "paid_at": paid_at,
        "status": item.status,
        "status_label": "Overdue" if overdue else item.get_status_display(),
        "is_overdue": overdue,
        "total_credits": total,
        "feature_credits": feature,
        "storage_credits": storage,
        "details": details if isinstance(details, dict) else {},
        "_order": issued_at,
    }


def company_saas_invoices(company, *, limit=40, now=None):
    """Recent actual documents from the three separate credit billing engines."""
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    now = now or timezone.now()
    rows = []
    for source, model in DOCUMENT_MODELS.items():
        # Bound every query and do not load unlimited documents.
        order_field = {
            "calendar": "-finalized_at",
            "monthly": "-created_at",
            "cycle": "-created_at",
        }[source]
        for item in model.objects.filter(company=company).order_by(order_field, "-pk")[:limit]:
            rows.append(_normalize(source, item, now=now))
    rows.sort(key=lambda row: (row["_order"], row["source"], row["pk"]), reverse=True)
    result = rows[:limit]
    for row in result:
        row.pop("_order", None)
    return result


def get_company_saas_invoice(company, source, pk, *, now=None):
    """Fail closed: unknown source, document ID or another tenant raises 404."""
    model = DOCUMENT_MODELS.get(source)
    if model is None:
        from django.http import Http404
        raise Http404("Unknown SaaS billing document")
    item = get_object_or_404(model, company=company, pk=pk)
    result = _normalize(source, item, now=now or timezone.now())
    result.pop("_order", None)
    return result
