"""Credits API: read-only wallet views and signed Razorpay top-up transactions."""
import json
import uuid
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import UserRateThrottle

from apps.accounts.permissions import user_has_permission, IsCompanyOwner
from apps.saas.models import PlanFeature
from apps.saas.razorpay_client import configured, create_order, fetch_payment, verify_checkout, verify_webhook, GatewayError
from .models import CreditLedger, CreditPackage, CreditPurchase, FeatureCreditPrice, CompanyCreditFeature, CreditWebhookEvent, CompanyPostpaidFeature, FeatureMeterPeriod
from .service import wallet_for, staff_count, billable_user_count, price_for, enable_paid_feature, disable_paid_feature, confirm_purchase, change_balance
from apps.saas.access import FREE_TENANT_MODULES
from .monthly_features import activate_postpaid_feature, deactivate_postpaid_feature
from .hosted_checkout import reconcile_hosted_purchase


class CreditsOrderThrottle(UserRateThrottle):
    rate = "10/hour"


class CreditsVerifyThrottle(UserRateThrottle):
    rate = "30/hour"


class CompanyCreditsView(APIView):
    permission_classes = [IsCompanyOwner]

    def get(self, request):
        if not request.user.company_id:
            return Response({"message": "A workshop company is required."}, status=403)
        company = request.user.company
        wallet = wallet_for(company)
        count = staff_count(company)
        users = billable_user_count(company)
        if settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED:
            from .models import FeatureUsageSession
            active = CompanyCreditFeature.objects.filter(
                company=company, enabled=True,
                feature_id__in=FeatureUsageSession.objects.filter(
                    company=company, ended_at__isnull=True,
                ).values("feature_id"),
            ).select_related("feature")
        else:
            active = CompanyCreditFeature.objects.filter(
                company=company, enabled=True, next_charge_at__gt=timezone.now()
            ).select_related("feature")
        postpaid = CompanyPostpaidFeature.objects.none()
        pending_postpaid = Decimal("0.00")
        if settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED:
            postpaid = CompanyPostpaidFeature.objects.filter(
                company=company, enabled=True, suspended=False,
            )
            pending_postpaid = sum((
                period.accrued_credits + period.activation_credits
                for period in FeatureMeterPeriod.objects.filter(
                    company=company, settled_at__isnull=True,
                )
            ), Decimal("0.00"))
        postpaid_count = postpaid.count()
        prices = {p.feature_id: p for p in FeatureCreditPrice.objects.filter(feature_id__in=[f.feature_id for f in active])}
        monthly = sum(
            (price_for(prices[state.feature_id], count, users) * Decimal("30") /
             Decimal({"daily": 1, "weekly": 7, "monthly": 30}.get(prices[state.feature_id].billing_period, 30))
             for state in active if state.feature_id in prices),
            Decimal("0.00"),
        )
        # The amount already owed survives an OFF action. Metered rows
        # remain unsettled regardless of how many modules are still enabled.
        from .period_rates import PERIOD_SECONDS
        from django.db.models import Sum
        if settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED:
            totals = FeatureMeterPeriod.objects.filter(
                company=company, settled_at__isnull=True,
            ).aggregate(usage=Sum("accrued_credits"), one_time=Sum("activation_credits"))
            pending_postpaid = (totals["usage"] or Decimal("0")) + (totals["one_time"] or Decimal("0"))
        else:
            pending_postpaid = Decimal("0")
        # Estimates assume uninterrupted operation for 30 days and are
        # informational only: the ledger uses observed seconds, not estimates.
        projected_postpaid = sum((
            state.monthly_rate * Decimal(30 * 24 * 60 * 60)
            / Decimal(state.rate_duration_seconds or PERIOD_SECONDS["monthly"])
            for state in postpaid if state.billing_period != "permanent"
        ), Decimal("0"))

        return Response({
            "balance": str(wallet.balance),
            "lowThreshold": str(wallet.low_threshold),
            "lowBalance": wallet.balance <= wallet.low_threshold,
            "staffCount": count,
            "userCount": users,
            "activeFeatures": active.count() + postpaid_count,
            "monthlyCredits": str(monthly + projected_postpaid),
            "postpaidAccruedCredits": str(pending_postpaid.quantize(Decimal("0.01"))),
            "checkoutAvailable": configured(),
            "canManage": user_has_permission(request.user, "billing.edit"),
        })


class CreditPackagesView(APIView):
    permission_classes = [IsCompanyOwner]

    def get(self, request):
        if not request.user.company_id:
            return Response({"message": "Workshop account required."}, status=403)
        return Response([
            {"id": p.id, "name": p.name, "credits": str(p.credits),
             "priceInr": str(p.price_inr), "active": p.is_active}
            for p in CreditPackage.objects.filter(is_active=True, is_archived=False)
        ])


class CreditFeaturesView(APIView):
    permission_classes = [IsCompanyOwner]

    def get(self, request):
        if not request.user.company_id:
            return Response({"message": "Workshop account required."}, status=403)
        if getattr(settings, 'CREDIT_LABEL_ONLY_ACCESS_ENABLED', False):
            return Response([])
        count = staff_count(request.user.company)
        users = billable_user_count(request.user.company)
        current = {v.feature_id: v for v in CompanyCreditFeature.objects.filter(company=request.user.company)}
        postpaid_open_features = set()
        if settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED:
            from .models import FeatureUsageSession
            postpaid_open_features = set(
                FeatureUsageSession.objects.filter(
                    company=request.user.company, ended_at__isnull=True,
                ).values_list("feature_id", flat=True)
            )
        monthly_states = {}
        if settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED:
            monthly_states = {
                state.feature_id: state for state in CompanyPostpaidFeature.objects.filter(
                    company=request.user.company,
                )
            }
        rows = FeatureCreditPrice.objects.filter(feature__is_active=True).exclude(
            feature__key__in=FREE_TENANT_MODULES
        ).select_related("feature").order_by("feature__sort_order", "feature__label")
        return Response([
            {
                "key": rate.feature.key, "label": rate.feature.label,
                "description": rate.feature.description,
                "baseCredits": str(rate.base_credits),
                "activationCredits": str(rate.activation_credits),
                "perUserCredits": str(rate.per_user_credits),
                "userCount": users,
                "billingPeriod": rate.billing_period,
                "billingMode": (
                    "monthly_postpaid"
                    if settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED
                    or (settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED and rate.billing_period == "monthly")
                    else "prepaid"
                ),
                "periodCredits": str(price_for(rate, count, users)),
                "perStaffCredits": str(rate.per_staff_credits),
                "staffCount": count,
                "monthlyCredits": str((price_for(rate, count, users) *
                    Decimal("30") / Decimal({"daily": 1, "weekly": 7, "monthly": 30}[rate.billing_period])
                    ).quantize(Decimal("0.01"))),
                "enabled": bool(
                    (
                        monthly_states.get(rate.feature_id)
                        and monthly_states[rate.feature_id].enabled
                        and not monthly_states[rate.feature_id].suspended
                    ) if (settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED
                          and rate.billing_period == "monthly")
                    else (
                        current.get(rate.feature_id) and current[rate.feature_id].enabled and (
                            rate.feature_id in postpaid_open_features
                            if settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED
                            else (
                                current[rate.feature_id].next_charge_at
                                and current[rate.feature_id].next_charge_at > timezone.now()
                            )
                        )
                    )
                ),
                "suspended": bool(
                    monthly_states.get(rate.feature_id)
                    and monthly_states[rate.feature_id].suspended
                ),
                "nextChargeAt": (
                    current[rate.feature_id].next_charge_at.isoformat()
                    if current.get(rate.feature_id) and current[rate.feature_id].next_charge_at else None
                ),
                "available": rate.is_available and price_for(rate, count, users) > 0,
                "activationEnabled": bool(settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED or request.user.company.require_paid_features),
            }
            for rate in rows
        ])


class FeatureInput(serializers.Serializer):
    key = serializers.SlugField(max_length=100)


def record_feature_change(request, feature, enabled):
    from apps.saas.models import SecurityEvent
    SecurityEvent.objects.create(
        company=request.user.company, user=request.user,
        event="feature_activated" if enabled else "feature_disabled",
        metadata={"feature": feature.key, "name": feature.label},
    )


class EnableCreditFeatureView(APIView):
    permission_classes = [IsCompanyOwner]

    @transaction.atomic
    def post(self, request):
        if not request.user.company_id or not user_has_permission(request.user, "billing.edit"):
            return Response({"message": "Company billing administrator permission required."}, status=403)
        if getattr(settings, 'CREDIT_LABEL_ONLY_ACCESS_ENABLED', False):
            return Response({
                'code': 'LABEL_ONLY_ACCESS_ENABLED',
                'message': 'Activate a Feature Label instead of an individual module.',
            }, status=409)
        # Never charge a user for a paid toggle that would have no effect.
        if not (settings.CREDIT_FEATURE_ENFORCEMENT_ENABLED or request.user.company.require_paid_features):
            return Response({
                "code": "FEATURE_ENTITLEMENTS_NOT_ENABLED",
                "message": "Paid feature access must be enabled by the platform administrator before activating paid features.",
            }, status=409)
        from .monthly_policy import billing_status
        if billing_status(request.user.company)['blocked']:
            return Response({'code': 'MONTHLY_PAYMENT_OVERDUE', 'message': 'Settle outstanding usage before activating another module.'}, status=409)
        payload = FeatureInput(data=request.data)
        payload.is_valid(raise_exception=True)
        feature = get_object_or_404(PlanFeature, key=payload.validated_data["key"], is_active=True)
        rate = FeatureCreditPrice.objects.filter(feature=feature, is_available=True).first()
        if not rate or price_for(rate, staff_count(request.user.company), billable_user_count(request.user.company)) <= 0:
            return Response({
                'code': 'MODULE_PRICE_NOT_SET',
                'message': 'A positive module price must be configured before activation.',
            }, status=409)
        try:
            if settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED:
                state, charge = activate_postpaid_feature(request.user.company, feature)
                record_feature_change(request, feature, True)
                return Response({
                    "enabled": state.enabled, "chargedCredits": str(charge),
                    "nextChargeAt": None, "billingMode": "monthly_postpaid",
                })
            state, charge = enable_paid_feature(request.user.company, feature, request.user)
            record_feature_change(request, feature, True)
        except ValueError as exc:
            return Response({"message": str(exc)}, status=409)
        return Response({"enabled": state.enabled, "chargedCredits": str(charge), "nextChargeAt": state.next_charge_at,
                         "postpaid": bool(settings.CREDIT_POSTPAID_FEATURE_BILLING_ENABLED)})


class DisableCreditFeatureView(APIView):
    permission_classes = [IsCompanyOwner]

    @transaction.atomic
    def post(self, request):
        if not request.user.company_id or not user_has_permission(request.user, "billing.edit"):
            return Response({"message": "Company billing administrator permission required."}, status=403)
        payload = FeatureInput(data=request.data)
        payload.is_valid(raise_exception=True)
        feature = get_object_or_404(PlanFeature, key=payload.validated_data["key"], is_active=True)
        if settings.CREDIT_FEATURE_POSTPAID_MONTHLY_ENABLED:
            existing = CompanyPostpaidFeature.objects.filter(
                company=request.user.company, feature=feature, enabled=True,
            ).exists()
            if existing:
                deactivate_postpaid_feature(request.user.company, feature)
                record_feature_change(request, feature, False)
                return Response({"enabled": False, "billingMode": "monthly_postpaid"})
        disable_paid_feature(request.user.company, feature, request.user)
        record_feature_change(request, feature, False)
        return Response({"enabled": False})


class CreditLogsView(APIView):
    permission_classes = [IsCompanyOwner]

    def get(self, request):
        if not request.user.company_id:
            return Response({"message": "Workshop account required."}, status=403)
        try:
            limit = int(request.query_params.get("limit", "100"))
        except (ValueError, TypeError):
            return Response({"message": "Invalid limit."}, status=400)
        if not 1 <= limit <= 100:
            return Response({"message": "Limit must be 1-100."}, status=400)
        rows = CreditLedger.objects.filter(company=request.user.company)[:limit]
        return Response([
            {"id": str(row.pk), "kind": row.kind, "description": row.description,
             "delta": str(row.delta), "balanceAfter": str(row.balance_after),
             "reference": row.reference, "createdAt": row.created_at.isoformat()}
            for row in rows
        ])


class PackageInput(serializers.Serializer):
    packageId = serializers.IntegerField(min_value=1)


class CreateCreditOrderView(APIView):
    permission_classes = [IsCompanyOwner]
    throttle_classes = [CreditsOrderThrottle]

    @transaction.atomic
    def post(self, request):
        if not request.user.company_id or not user_has_permission(request.user, "billing.edit"):
            return Response({"message": "Company billing administrator permission required."}, status=403)
        validated = PackageInput(data=request.data)
        validated.is_valid(raise_exception=True)
        package = get_object_or_404(CreditPackage, id=validated.validated_data["packageId"], is_active=True, is_archived=False)
        amount = int((package.price_inr * Decimal("100")).to_integral_exact())
        if package.credits <= 0 or amount < 100 or amount > 10000000000:
            return Response({"message": "Invalid published credit package."}, status=400)
        if not configured():
            return Response({
                "code": "RAZORPAY_NOT_CONFIGURED",
                "message": "Razorpay is not configured on the backend server.",
            }, status=503)
        receipt = "CW" + uuid.uuid4().hex[:32]
        purchase = CreditPurchase.objects.create(
            company=request.user.company, requested_by=request.user, package=package,
            credits=package.credits, amount_paise=amount, receipt=receipt,
        )
        try:
            remote = create_order(amount, receipt, request.user.company_id)
            if (not isinstance(remote, dict)
                or not str(remote.get("id", "")).startswith("order_")
                or remote.get("amount") != amount or remote.get("currency") != "INR"):
                raise GatewayError("Unexpected Razorpay response.")
        except GatewayError as exc:
            purchase.delete()
            if exc.reason == "invalid_credentials":
                return Response({
                    "code": "RAZORPAY_AUTH_FAILED",
                    "message": "Payment gateway authentication failed. Please contact CubeGear support.",
                }, status=503)
            if exc.reason == "provider_rejected":
                return Response({
                    "code": "RAZORPAY_ORDER_REJECTED",
                    "message": "Payment provider rejected the order. Please contact CubeGear support.",
                }, status=503)
            return Response({
                "code": "RAZORPAY_UNAVAILABLE",
                "message": "The payment gateway is temporarily unavailable. Please try again.",
            }, status=503)
        purchase.order_id = remote["id"]
        purchase.save(update_fields=["order_id"])
        return Response({
            "keyId": settings.RAZORPAY_KEY_ID, "orderId": purchase.order_id,
            "amount": purchase.amount_paise, "credits": str(purchase.credits),
            "prefill": {"name": request.user.name, "email": request.user.email},
        }, status=201)


class CreditVerifyInput(serializers.Serializer):
    razorpay_order_id = serializers.CharField(max_length=80)
    razorpay_payment_id = serializers.CharField(max_length=80)
    razorpay_signature = serializers.CharField(max_length=128)


class VerifyCreditPaymentView(APIView):
    permission_classes = [IsCompanyOwner]
    throttle_classes = [CreditsVerifyThrottle]

    def post(self, request):
        if not request.user.company_id:
            return Response({"message": "Workshop account required."}, status=403)
        payload = CreditVerifyInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        purchase = CreditPurchase.objects.filter(
            company=request.user.company, order_id=data["razorpay_order_id"],
        ).first()
        if not purchase:
            return Response({"message": "Unknown credit purchase."}, status=404)
        if purchase.requested_by_id != request.user.pk and not user_has_permission(request.user, "billing.edit"):
            return Response({"message": "Billing administrator permission required."}, status=403)
        if not configured() or not verify_checkout(
            purchase.order_id, data["razorpay_payment_id"], data["razorpay_signature"]
        ):
            return Response({"message": "Invalid payment signature."}, status=400)
        try:
            payment = fetch_payment(data["razorpay_payment_id"])
            result = confirm_purchase(purchase.pk, payment)
        except GatewayError:
            return Response({"message": "Payment pending confirmation. No credits granted yet."}, status=503)
        except ValueError:
            return Response({"message": "Payment status, order or amount does not match."}, status=409)
        return Response({"credited": result.status == "credited", "credits": str(result.credits)})


class CreditWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request):
        body = request._request.body
        if len(body) > 262144:
            return Response({"message": "Webhook too large."}, status=413)
        signature = request.headers.get("X-Razorpay-Signature", "")
        delivery_id = request.headers.get("X-Razorpay-Event-Id", "")
        if not delivery_id or len(delivery_id) > 150 or not verify_webhook(body, signature):
            return Response({"message": "Invalid webhook signature."}, status=400)
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return Response({"message": "Invalid webhook payload."}, status=400)
        kind = str(data.get("event") or "")[:100]
        envelope = data.get("payload") or {}
        if not isinstance(envelope, dict):
            return Response({"message": "Invalid webhook payload."}, status=400)
        # A signed Payment Link event can confirm payment even when the buyer
        # closes the browser before returning to CubeGear.
        if kind == "payment_link.paid":
            link = ((envelope.get("payment_link") or {}).get("entity") or {})
            link_id = link.get("id") if isinstance(link, dict) else None
            purchase = CreditPurchase.objects.filter(payment_link_id=link_id).first() if link_id else None
            if purchase and purchase.status == "pending":
                try:
                    outcome = reconcile_hosted_purchase(purchase)
                except GatewayError:
                    return Response({"message": "Provider verification pending."}, status=503)
                except ValueError:
                    return Response({"message": "Payment link verification failed."}, status=400)
                if outcome.status != "credited":
                    return Response({"message": "Payment capture pending."}, status=503)
        with transaction.atomic():
            if CreditWebhookEvent.objects.filter(event_id=delivery_id).exists():
                return Response({"received": True, "duplicate": True})
            if kind == "payment.captured":
                payment = ((envelope.get("payment") or {}).get("entity") or {})
                purchase = CreditPurchase.objects.filter(order_id=payment.get("order_id")).first()
                if purchase:
                    try:
                        confirm_purchase(purchase.pk, payment)
                    except ValueError:
                        return Response({"message": "Unmatched payment."}, status=400)
            elif kind == "refund.processed":
                payment = ((envelope.get("payment") or {}).get("entity") or {})
                refund = ((envelope.get("refund") or {}).get("entity") or {})
                purchase = CreditPurchase.objects.select_for_update().filter(
                    payment_id=payment.get("id"), status="credited"
                ).first()
                if (purchase and refund.get("status") == "processed"
                    and refund.get("payment_id") == purchase.payment_id
                    and int(payment.get("amount_refunded") or 0) >= purchase.amount_paise):
                    wallet_for(purchase.company)
                    change_balance(
                        purchase.company, -purchase.credits,
                        kind="refund", reference=f"credit-refund:{purchase.order_id or purchase.payment_link_id or purchase.pk}",
                        description=f"Fully refunded purchase {purchase.order_id or purchase.payment_link_id or purchase.pk}",
                        allow_negative=True,
                    )
                    purchase.status = "refunded"
                    purchase.save(update_fields=["status"])
            CreditWebhookEvent.objects.create(event_id=delivery_id, event_type=kind)
        return Response({"received": True})
