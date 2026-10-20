"""Company-scoped feature pricing, usage and wallet audit for platform admins.

No inferred history: catalog prices may change, while recorded usage sessions,
activation events and ledger debits retain their historical amounts. Never
sum different event sources as if they were independent charges.
"""
from django.utils import timezone

from apps.credits.models import (
    CompanyBundleAccess, CompanyPostpaidFeature, CompanyCreditFeature,
    FeatureCreditPrice, CreditLedger,
    CycleLabelSession,
    FeatureMeterReading,
    FeatureUsageSession,
)
from apps.saas.models import SecurityEvent


FEATURE_EVENT_NAMES = {
    "feature_label_activated": "Feature activated",
    "feature_label_resumed": "Feature resumed",
    "feature_label_disabled": "Feature disabled",
    "feature_label_cycle_activated": "Cycle activation",
    "feature_activated": "Module activated",
    "feature_disabled": "Module disabled",
}
FEATURE_LEDGER_KINDS = (
    "bundle_charge", "feature_charge", "feature_disabled",
    "feature_suspended", "bundle_use",
)


def company_feature_pricing_report(company, *, limit=50):
    """Show only actual recorded company data, newest first.

    Current catalog prices are never mistaken for a historical purchased rate.
    All querysets are tenant-scoped and bounded, including the final merge.
    """
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    entitlement_rows = []
    accesses = (
        CompanyBundleAccess.objects.filter(company=company)
        .select_related("bundle").prefetch_related("bundle__features")
        .order_by("-updated_at", "-pk")[:100]
    )
    cycle_bundle_ids = set(
        CycleLabelSession.objects.filter(company=company)
        .values_list("bundle_id", flat=True)
    )
    at = timezone.now()
    access_names = {}
    for access in accesses:
        access_names[access.bundle_id] = access.bundle.name
        is_cycle = access.bundle_id in cycle_bundle_ids
        entitlement_rows.append({
            "name": access.bundle.name,
            "modules": ", ".join(f.label for f in access.bundle.features.all()) or "—",
            "billing_period": "Monthly postpaid cycle" if is_cycle else access.get_billing_type_at_purchase_display(),
            "recorded_price": None if is_cycle else access.last_price_credits,
            # A bundle's catalog rate can change after purchase. This is
            # explicitly current, NOT a historical per-user price snapshot.
            "current_per_user_price": access.bundle.per_user_credits,
            "enabled": bool(access.enabled and access.bundle.is_active and
                            (access.valid_until is None or access.valid_until > at)),
            "valid_until": access.valid_until,
            "updated_at": access.updated_at,
        })

    # Metered monthly modules may be activated without a prepaid access row.
    # Show their CURRENT state/rates but never invent an activation payment.
    postpaid_states = (
        CompanyPostpaidFeature.objects.filter(company=company)
        .select_related("feature").order_by("-updated_at", "-pk")[:100]
    )
    feature_rates = {
        rate.feature_id: rate.per_user_credits
        for rate in FeatureCreditPrice.objects.filter(
            feature_id__in=[item.feature_id for item in postpaid_states]
        )
    }
    for state in postpaid_states:
        entitlement_rows.append({
            "name": state.feature.label,
            "modules": state.feature.label,
            "billing_period": "Monthly postpaid",
            "recorded_price": None,
            "current_per_user_price": feature_rates.get(state.feature_id),
            "enabled": state.enabled and not state.suspended,
            "valid_until": None,
            "updated_at": state.updated_at,
        })

    # Older standalone module entitlements also have tenant-owned state.
    standalone_states = (
        CompanyCreditFeature.objects.filter(company=company)
        .select_related("feature").order_by("-updated_at", "-pk")[:100]
    )
    standalone_rates = {
        rate.feature_id: rate.per_user_credits
        for rate in FeatureCreditPrice.objects.filter(
            feature_id__in=[state.feature_id for state in standalone_states]
        )
    }
    for state in standalone_states:
        entitlement_rows.append({
            "name": state.feature.label,
            "modules": state.feature.label,
            "billing_period": "Legacy standalone",
            "recorded_price": None,
            "current_per_user_price": standalone_rates.get(state.feature_id),
            "enabled": state.enabled,
            "valid_until": state.next_charge_at,
            "updated_at": state.updated_at,
        })
    entitlement_rows.sort(key=lambda item: item["updated_at"], reverse=True)

    history = []

    for item in (
        FeatureMeterReading.objects.filter(company=company)
        .select_related("feature").order_by("-sampled_at", "-pk")[:limit]
    ):
        history.append({
            "occurred_at": item.sampled_at,
            "name": item.feature.label,
            "activity": "Meter reading",
            "period": "Monthly",
            "users": item.users,
            "rate": item.monthly_rate,
            "credits": item.accrued_credits,
            "credit_kind": "Accrued",
            "details": f"{item.covered_seconds} seconds covered; {item.staff} staff",
            "source": "Meter",
            "unique_key": f"meter-{item.pk}",
        })

    for item in (
        CycleLabelSession.objects.filter(company=company)
        .select_related("bundle").order_by("-started_at", "-pk")[:limit]
    ):
        history.append({
            "occurred_at": item.started_at,
            "name": item.bundle.name,
            "activity": "Cycle rate snapshot",
            "period": "Monthly cycle",
            "users": item.user_seats,
            "rate": item.monthly_credits,
            "credits": None,
            "credit_kind": "Rate only",
            "details": "Closed" if item.ended_at else "Active session",
            "source": "Rate snapshot",
            "unique_key": f"cycle-{item.pk}",
        })

    for item in (
        FeatureUsageSession.objects.filter(company=company)
        .select_related("feature").order_by("-started_at", "-pk")[:limit]
    ):
        history.append({
            "occurred_at": item.started_at,
            "name": item.feature.label,
            "activity": "Usage rate snapshot",
            "period": "Monthly",
            "users": item.user_seats,
            "rate": item.monthly_credits,
            "credits": None,
            "credit_kind": "Rate only",
            "details": "Closed" if item.ended_at else "Active session",
            "source": "Rate snapshot",
            "unique_key": f"usage-{item.pk}",
        })

    for item in (
        CreditLedger.objects.filter(company=company, kind__in=FEATURE_LEDGER_KINDS)
        .select_related("actor").order_by("-created_at", "-pk")[:limit]
    ):
        history.append({
            "occurred_at": item.created_at,
            "name": item.description,
            "activity": {
                "bundle_charge": "Feature label charge",
                "feature_charge": "Feature charge",
                "feature_disabled": "Feature disabled",
                "feature_suspended": "Feature suspended",
                "bundle_use": "Feature operation",
            }.get(item.kind, item.kind),
            "period": "—",
            "users": None,
            "rate": None,
            "credits": item.delta,
            "credit_kind": "Wallet change",
            "details": item.reference,
            "source": "Wallet ledger",
            "unique_key": f"ledger-{item.pk}",
        })

    for item in (
        SecurityEvent.objects.filter(company=company, event__in=tuple(FEATURE_EVENT_NAMES))
        .select_related("user").order_by("-created_at", "-pk")[:limit]
    ):
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        bundle_id = metadata.get("bundleId")
        try:
            bundle_name = access_names.get(int(bundle_id)) if bundle_id is not None else None
        except (TypeError, ValueError):
            bundle_name = None
        history.append({
            "occurred_at": item.created_at,
            "name": str(metadata.get("name") or bundle_name or metadata.get("feature") or "Feature label"),
            "activity": FEATURE_EVENT_NAMES[item.event],
            "period": str(metadata.get("billingType") or "—").replace("_", " ").title(),
            "users": metadata.get("userCount") if isinstance(metadata.get("userCount"), int) else None,
            "rate": metadata.get("rateCreditsPerCycle"),
            # Activation fees may also have a wallet ledger entry. Display
            # recorded activation amount as info, not a second wallet debit.
            "credits": metadata.get("credits"),
            "credit_kind": "Recorded purchase" if metadata.get("credits") is not None else "Event only",
            "details": item.user.name if item.user_id and item.user else "System",
            "source": "Access event",
            "unique_key": f"event-{item.pk}",
        })

    history.sort(
        key=lambda row: (row["occurred_at"], row["unique_key"]), reverse=True,
    )
    return {
        "entitlements": entitlement_rows,
        "logs": history[:limit],
        "shown_count": min(len(history), limit),
    }
