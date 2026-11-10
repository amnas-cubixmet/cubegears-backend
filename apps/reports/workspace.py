"""One permission-aware source for the Reports dashboard.

Financial records are always derived from Django's authoritative invoice,
customer payment and expense tables; never from cached browser totals.
Permissions are enforced for EACH source even if reports.view is granted.
"""
from datetime import date
from decimal import Decimal

from django.db.models import Q, Sum
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import RolePermission, user_has_permission


MAX_REPORT_ROWS = 5000


def _date_parameter(request, key):
    raw = request.query_params.get(key, "")
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(raw)
    except (TypeError, ValueError):
        raise ValidationError({key: "Use a valid YYYY-MM-DD date."})
    if parsed.isoformat() != raw:
        raise ValidationError({key: "Use a valid YYYY-MM-DD date."})
    return parsed


def _rows(queryset):
    """Explicitly report truncation instead of misrepresenting partial totals."""
    rows = list(queryset[: MAX_REPORT_ROWS + 1])
    return rows[:MAX_REPORT_ROWS], len(rows) > MAX_REPORT_ROWS


class ReportsWorkspaceView(APIView):
    permission_classes = [RolePermission]
    permission_code = "reports.view"

    def get(self, request):
        from apps.expenses.models import Expense
        from apps.inventory.models import StockItem
        from apps.invoices.models import Invoice
        from apps.payments.models import Payment

        company = request.user.company
        if company is None:
            return Response({"detail": "Workshop membership is required."}, status=403)

        start = _date_parameter(request, "from")
        end = _date_parameter(request, "to")
        if start and end and start > end:
            raise ValidationError({"to": "End date must be on or after start date."})

        payload = {
            "company": {
                "name": company.name,
                "address": company.address,
                "gstin": company.gstin,
                "phone": company.phone,
                "email": company.email,
            },
            "sources": {},
            "invoices": [],
            "payments": [],
            "expenses": [],
            "stock": [],
        }

        def source(name, code, queryset, mapper):
            if not user_has_permission(request.user, code):
                payload["sources"][name] = {
                    "available": False,
                    "reason": "permission",
                    "truncated": False,
                }
                return
            records, truncated = _rows(queryset)
            payload[name] = [mapper(row) for row in records]
            payload["sources"][name] = {
                "available": True,
                "truncated": truncated,
            }

        def period(queryset):
            if start:
                queryset = queryset.filter(date__gte=start)
            if end:
                queryset = queryset.filter(date__lte=end)
            return queryset

        source(
            "invoices", "invoices.view",
            period(Invoice.objects.filter(company=company, kind="invoice"))
            .order_by("-date", "-created_at").values(
                "id", "number", "date", "kind", "status", "total", "paid",
                "balance", "customer__name", "vehicle__registration",
                "staff_name", "branch__name",
            ),
            lambda row: {
                "id": str(row["id"]), "number": row["number"],
                "date": row["date"], "kind": row["kind"],
                "status": row["status"], "total": row["total"],
                "paid": row["paid"], "balance": row["balance"],
                "customerName": row["customer__name"],
                "vehicleReg": row["vehicle__registration"] or "",
                "staff": row["staff_name"] or "",
                "branchName": row["branch__name"] or "",
            },
        )

        source(
            "payments", "payments.view",
            period(Payment.objects.filter(company=company))
            .order_by("-date", "-created_at").values(
                "id", "date", "status", "amount", "direction", "method",
                "reference", "reversed_at", "customer__name",
                "invoice__number", "recorded_by__name", "branch__name",
            ),
            lambda row: {
                "id": str(row["id"]), "date": row["date"],
                "status": row["status"], "amount": row["amount"],
                "direction": row["direction"], "method": row["method"],
                "reference": row["reference"],
                "reversed_at": row["reversed_at"],
                "customerName": row["customer__name"] or "",
                "invoiceNo": row["invoice__number"] or "",
                "staff": row["recorded_by__name"] or "",
                "branchName": row["branch__name"] or "",
            },
        )

        expense_qs = period(
            Expense.objects.filter(company=company)
        ).annotate(
            paid_amount=Sum(
                "settlements__amount",
                filter=Q(settlements__reversed_at__isnull=True),
            )
        ).order_by("-date", "-created_at").values(
            "id", "date", "status", "category", "vendor", "description",
            "amount", "paid_amount", "payment_method", "reference",
            "created_by__name", "branch__name",
        )

        source(
            "expenses", "expenses.view", expense_qs,
            lambda row: {
                "id": str(row["id"]), "date": row["date"],
                "status": row["status"], "category": row["category"],
                "vendor": row["vendor"], "description": row["description"],
                "title": (row["description"] or "").split("\n\n", 1)[0],
                "amount": row["amount"],
                "paidAmount": row["paid_amount"] or Decimal("0.00"),
                "paymentMethod": row["payment_method"],
                "reference": row["reference"],
                "staff": row["created_by__name"] or "",
                "branchName": row["branch__name"] or "",
            },
        )

        # Inventory is a CURRENT company-wide snapshot and cannot use the
        # financial date filter; it is not a historic movement valuation.
        source(
            "stock", "stock.view",
            StockItem.objects.filter(company=company).order_by("name").values(
                "id", "name", "sku", "category__name",
                "on_hand", "reserved", "minimum_stock", "cost_price",
            ),
            lambda row: {
                "id": str(row["id"]), "name": row["name"],
                "sku": row["sku"], "category": row["category__name"] or "",
                "onHand": row["on_hand"], "reserved": row["reserved"],
                "minimumStock": row["minimum_stock"],
                "costPrice": row["cost_price"],
            },
        )

        return Response(payload)
