"""Tenant-scoped prepaid feature label catalog and activation API."""
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.saas.feature_discovery import module_inventory
from apps.saas.access import FREE_TENANT_MODULES
from apps.accounts.permissions import user_has_permission, IsCompanyOwner
from .models import FeatureBundle, CompanyBundleAccess
from .service import purchase_feature_bundle, disable_feature_bundle, bundle_is_usable, billable_user_count
from .cycle_billing import effective_policy, activate_cycle_label


class BundlePurchaseThrottle(UserRateThrottle):
    rate = "12/hour"


class BundleInput(serializers.Serializer):
    bundleId = serializers.IntegerField(min_value=1)


def _public_bundle(bundle, access=None, user_count=0, company=None):
    active_features = [
        {"key": feature.key, "label": feature.label}
        for feature in bundle.features.all() if feature.is_active
    ]
    # Expose the platform's real readiness state, not a browser-only toggle.
    disabled_reason = (
        "POSTPAID_MONTHLY_ONLY"
        if effective_policy() and bundle.billing_type != "monthly"
        else "FEATURE_ENTITLEMENTS_NOT_ENABLED"
        if not (settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED or (company and company.require_paid_features))
        else "METERED_OPERATION_NOT_READY"
        if bundle.billing_type == "per_use"
        else "FEATURE_LABEL_EMPTY"
        if not active_features
        else "FREE_MODULES_ONLY"
        if all(item["key"] in FREE_TENANT_MODULES for item in active_features)
        else "INVALID_USER_PRICE"
        if not Decimal("0.01") <= bundle.per_user_credits <= Decimal("1000000.00")
        else None
    )
    return {
        "id": bundle.pk, "key": bundle.key, "name": bundle.name,
        "description": bundle.description,
        "perUserCredits": str(bundle.per_user_credits),
        "userCount": user_count,
        "totalCredits": str((bundle.per_user_credits * user_count).quantize(Decimal("0.01"))),
        "billingType": bundle.billing_type,
        "features": active_features,
        "freeModules": [feature["key"] for feature in active_features if feature["key"] in FREE_TENANT_MODULES],
        "active": bundle_is_usable(access) if access else False,
        "canResume": bool(access and not access.enabled and access.last_price_credits > 0 and access.purchased_at and (
            access.billing_type_at_purchase == "permanent" or (
                access.billing_type_at_purchase in {"hourly", "daily", "weekly", "monthly"}
                and access.valid_until and access.valid_until > timezone.now()
            )
        )),
        "validUntil": access.valid_until.isoformat() if access and access.valid_until else None,
        "usesRemaining": access.uses_remaining if access else 0,
        "meteredReady": bundle.billing_type != "per_use",
        "purchaseEnabled": disabled_reason is None,
        "purchaseDisabledReason": disabled_reason,
        "cyclePostpaid": bool(company and effective_policy() and
            bundle.billing_type == "monthly"),
    }


class BundleListView(APIView):
    permission_classes = [IsCompanyOwner]

    def get(self, request):
        if not request.user.company_id:
            return Response({"message": "Workshop account required."}, status=403)
        # Old published labels can still reference Dashboard/Settings/Staff.
        # Exclude free-only labels from the paid marketplace rather than
        # presenting free core access as an activation or renewal product.
        bundles = [
            bundle
            for bundle in FeatureBundle.objects.filter(is_active=True).prefetch_related("features")
            if not bundle.features.all() or any(
                feature.key not in FREE_TENANT_MODULES
                for feature in bundle.features.all()
            )
        ]
        accesses = {
            a.bundle_id: a for a in CompanyBundleAccess.objects.filter(
                company=request.user.company, bundle_id__in=[b.pk for b in bundles]
            ).select_related("bundle")
        }
        count = billable_user_count(request.user.company)
        rows = [_public_bundle(b, accesses.get(b.id), count, request.user.company) for b in bundles]
        represented = {feature['key'] for row in rows for feature in row['features']}
        # Read-only placeholders: listing a module must never create a free offer.
        for module in module_inventory():
            if not module['billable'] or module['key'] in represented:
                continue
            rows.append({
                'id': None, 'key': module['key'], 'name': module['label'],
                'description': 'A SaaS administrator must publish a price before activation.',
                'perUserCredits': '0.00', 'userCount': count, 'totalCredits': '0.00',
                'billingType': 'monthly', 'features': [{'key': module['key'], 'label': module['label']}],
                'active': False, 'canResume': False, 'validUntil': None,
                'usesRemaining': 0, 'meteredReady': True,
                'purchaseEnabled': False, 'purchaseDisabledReason': 'MODULE_PRICE_NOT_SET',
            })
        return Response(rows)


class BundlePurchaseView(APIView):
    permission_classes = [IsCompanyOwner]
    throttle_classes = [BundlePurchaseThrottle]

    def post(self, request):
        if not request.user.company_id or not user_has_permission(request.user, "billing.edit"):
            return Response({"message": "Company billing administrator permission required."}, status=403)
        payload = BundleInput(data=request.data)
        payload.is_valid(raise_exception=True)
        bundle = get_object_or_404(
            FeatureBundle, pk=payload.validated_data["bundleId"], is_active=True
        )
        if not (settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED or request.user.company.require_paid_features):
            return Response({
                "message": "Paid feature enforcement must be enabled by the platform administrator before purchasing labels.",
                "code": "FEATURE_ENTITLEMENTS_NOT_ENABLED",
            }, status=409)
        if bundle.billing_type == "per_use":
            # Do not sell an unredeemable token until real server-side feature
            # operations have been wired to consume_bundle_use().
            return Response({
                "message": "Per-use billing needs server-side usage tracking for the selected features before it can be purchased.",
                "code": "METERED_OPERATION_NOT_READY",
            }, status=409)
        try:
            if effective_policy():
                access, charged = activate_cycle_label(request.user.company, bundle, request.user)
            else:
                access, charged = purchase_feature_bundle(request.user.company, bundle, request.user)
        except ValueError as exc:
            return Response({"message": str(exc)}, status=409)
        return Response({
            "bundle": _public_bundle(bundle, access, billable_user_count(request.user.company), request.user.company),
            "chargedCredits": str(charged),
        })


class BundleDisableView(APIView):
    permission_classes = [IsCompanyOwner]

    def post(self, request):
        if not request.user.company_id or not user_has_permission(request.user, "billing.edit"):
            return Response({"message": "Company billing administrator permission required."}, status=403)
        payload = BundleInput(data=request.data)
        payload.is_valid(raise_exception=True)
        bundle = get_object_or_404(FeatureBundle, pk=payload.validated_data["bundleId"])
        access = disable_feature_bundle(request.user.company, bundle, actor=request.user)
        if not access:
            return Response({"message": "This company has not purchased this label."}, status=404)
        return Response({"bundle": _public_bundle(bundle, access, billable_user_count(request.user.company), request.user.company), "chargedCredits": "0.00"})
