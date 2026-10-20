"""Module price periods are rate units; collection remains calendar-monthly.

Times are fixed for rate calculation. A 'Monthly' module rate is 30 days
(2,592,000 seconds) even though invoices follow calendar-month boundaries.
Permanent is a single one-time usage charge at activation, not an infinite
recurring rate.
"""
from decimal import Decimal

PERIOD_SECONDS = {
    "ten_seconds": 10,
    "hourly": 60 * 60,
    "daily": 24 * 60 * 60,
    "weekly": 7 * 24 * 60 * 60,
    "monthly": 30 * 24 * 60 * 60,
}
PERIOD_LABELS = {
    "ten_seconds": "10 seconds",
    "hourly": "hour",
    "daily": "day",
    "weekly": "week",
    "monthly": "30 days",
    "permanent": "lifetime",
}


def period_seconds(period):
    if period == "permanent":
        return 0
    if period not in PERIOD_SECONDS:
        raise ValueError("Unsupported module billing period.")
    return PERIOD_SECONDS[period]


def period_usage_credits(rate, duration_seconds, covered_seconds):
    """Precise, nonnegative prorated credits for observed active seconds."""
    seconds = int(duration_seconds)
    if seconds <= 0:
        return Decimal("0")
    return Decimal(rate) * Decimal(max(0, int(covered_seconds))) / Decimal(seconds)
