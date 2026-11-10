"""Financial-year comparison for the printable CubixGear business report.

Indian FY: April–March. Values are authoritative billed invoices and completed
customer collections, not sample/demo metrics. Each dataset has its own permission.
"""
from datetime import date
from decimal import Decimal

from django.db.models import Q, Sum

from apps.accounts.permissions import user_has_permission

ZERO = Decimal("0.00")


def fiscal_year(value):
    """Return start year of the April–March financial year containing a date."""
    return value.year if value.month >= 4 else value.year - 1


def fy_label(start_year):
    return f"FY {start_year}–{str(start_year + 1)[-2:]}"


def quarter_bounds(start_year):
    starts = [
        date(start_year, 4, 1),
        date(start_year, 7, 1),
        date(start_year, 10, 1),
        date(start_year + 1, 1, 1),
        date(start_year + 1, 4, 1),
    ]
    return list(zip(starts, starts[1:]))


def _money(value):
    return value if value is not None else ZERO


def _annual_change(previous, current):
    if not previous:
        return None
    return round((current - previous) * Decimal("100") / previous, 2)


def _method_name(method):
    raw = str(method or "").lower().strip()
    if "upi" in raw:
        return "UPI"
    if "cash" in raw:
        return "Cash"
    if "bank" in raw or "neft" in raw or "imps" in raw or "rtgs" in raw:
        return "Bank transfer"
    if "card" in raw:
        return "Card"
    if "cheque" in raw or "check" in raw:
        return "Cheque"
    return "Other"


def get_fiscal_comparison(user, start_year):
    from apps.expenses.models import Expense
    from apps.invoices.models import Invoice
    from apps.payments.models import Payment

    company = user.company
    previous_year = start_year - 1
    has_invoices = user_has_permission(user, "invoices.view")
    has_payments = user_has_permission(user, "payments.view")
    has_expenses = user_has_permission(user, "expenses.view")
    result = {
        "startYear": start_year,
        "currentLabel": fy_label(start_year),
        "previousLabel": fy_label(previous_year),
        "scope": "Company-wide, April–March financial years",
        "available": {
            "invoices": has_invoices,
            "payments": has_payments,
            "expenses": has_expenses,
        },
        "quarters": [],
        "channels": [],
        "totals": {},
    }

    totals = {}
    if has_invoices:
        statuses = (
            Q(status__iexact="draft") | Q(status__iexact="cancelled")
            | Q(status__iexact="canceled") | Q(status__iexact="void")
        )
        qs = Invoice.objects.filter(
            company=company, kind="invoice",
        ).exclude(statuses)
        quarter_filters = {}
        for label, year in (("previous", previous_year), ("current", start_year)):
            for index, (start, end) in enumerate(quarter_bounds(year), 1):
                quarter_filters[f"{label}_{index}"] = Sum(
                    "total", filter=Q(date__gte=start, date__lt=end)
                )
        quarter_totals = qs.aggregate(**quarter_filters)
        for index in range(1, 5):
            previous = _money(quarter_totals[f"previous_{index}"])
            current = _money(quarter_totals[f"current_{index}"])
            result["quarters"].append({
                "quarter": f"Q{index}",
                "months": ("Apr–Jun", "Jul–Sep", "Oct–Dec", "Jan–Mar")[index - 1],
                "previous": previous,
                "current": current,
                "changePct": _annual_change(previous, current),
            })
        totals["invoices"] = (
            sum((row["previous"] for row in result["quarters"]), ZERO),
            sum((row["current"] for row in result["quarters"]), ZERO),
        )

    if has_payments:
        payments = Payment.objects.filter(
            company=company,
            status__iexact="completed",
            reversed_at__isnull=True,
        ).filter(
            date__gte=date(previous_year, 4, 1),
            date__lt=date(start_year + 1, 4, 1),
        )
        grouped = payments.values("method", "direction").annotate(
            previous_total=Sum(
                "amount", filter=Q(
                    date__gte=date(previous_year, 4, 1),
                    date__lt=date(previous_year + 1, 4, 1),
                )
            ),
            current_total=Sum(
                "amount", filter=Q(
                    date__gte=date(start_year, 4, 1),
                    date__lt=date(start_year + 1, 4, 1),
                )
            ),
        )
        by_channel = {}
        for row in grouped:
            method = _method_name(row["method"])
            previous, current = by_channel.get(method, (ZERO, ZERO))
            sign = -1 if str(row["direction"] or "IN").upper() == "OUT" else 1
            by_channel[method] = (
                previous + sign * _money(row["previous_total"]),
                current + sign * _money(row["current_total"]),
            )
        for method in ("Cash", "UPI", "Bank transfer", "Card", "Cheque", "Other"):
            if method not in by_channel:
                continue
            previous, current = by_channel[method]
            result["channels"].append({
                "channel": method, "previous": previous,
                "current": current, "changePct": _annual_change(previous, current),
            })
        totals["payments"] = (
            sum((v[0] for v in by_channel.values()), ZERO),
            sum((v[1] for v in by_channel.values()), ZERO),
        )

    if has_expenses:
        expenses = Expense.objects.filter(company=company).exclude(
            Q(status__iexact="draft") | Q(status__iexact="cancelled")
            | Q(status__iexact="canceled") | Q(status__iexact="void")
        ).aggregate(
            previous=Sum("amount", filter=Q(
                date__gte=date(previous_year, 4, 1),
                date__lt=date(previous_year + 1, 4, 1),
            )),
            current=Sum("amount", filter=Q(
                date__gte=date(start_year, 4, 1),
                date__lt=date(start_year + 1, 4, 1),
            )),
        )
        totals["expenses"] = (_money(expenses["previous"]), _money(expenses["current"]))

    for name, (previous, current) in totals.items():
        result["totals"][name] = {
            "previous": previous,
            "current": current,
            "changePct": _annual_change(previous, current),
        }
    return result
