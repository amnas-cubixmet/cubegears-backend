"""Authenticated plan checkout, server verification and signed Razorpay webhooks."""
import json
import uuid
from decimal import Decimal
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import UserRateThrottle
from apps.accounts.permissions import user_has_permission
from .models import SubscriptionPlan, RazorpayPlanPayment, RazorpayWebhookDelivery
from .billing_service import access_state, capture_order, refund_order, public_payment
from .razorpay_client import (
    GatewayError, configured, create_order, fetch_payment,
    verify_checkout, verify_webhook,
)


class CheckoutOrderThrottle(UserRateThrottle):
    rate = "10/hour"


class CheckoutVerifyThrottle(UserRateThrottle):
    rate = "30/hour"


class OrderInput(serializers.Serializer):
    plan_id = serializers.IntegerField(min_value=1)


class VerifyInput(serializers.Serializer):
    razorpay_order_id = serializers.CharField(max_length=80)
    razorpay_payment_id = serializers.CharField(max_length=80)
    razorpay_signature = serializers.CharField(max_length=128)


class CheckoutStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.company_id:
            return Response({"message": "A company account is required."}, status=403)
        return Response({
            **access_state(request.user.company),
            "checkoutAvailable": configured(),
            "canPurchase": user_has_permission(request.user, "billing.edit"),
        })


class CheckoutCreateView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [CheckoutOrderThrottle]

    def post(self, request):
        if not request.user.company_id or not user_has_permission(request.user, "billing.edit"):
            return Response({"message": "Company billing admin permission is required."}, status=403)
        data = OrderInput(data=request.data)
        data.is_valid(raise_exception=True)
        plan = SubscriptionPlan.objects.filter(
            id=data.validated_data["plan_id"], is_active=True, is_public=True
        ).first()
        if not plan:
            return Response({"message": "Plan is unavailable."}, status=404)
        # This is one one-time package, not recurring monthly/yearly billing.
        cycle = "term"
        amount = plan.checkout_price
        amount_paise = int((amount * Decimal(100)).to_integral_exact())
        if amount_paise < 100 or amount_paise > 10000000000:
            return Response({"message": "This plan cannot be purchased online."}, status=400)
        if not 1 <= plan.access_duration_days <= 730:
            return Response({"message": "Invalid configured plan validity."}, status=400)
        if not configured():
            return Response({"message": "Online checkout is not yet available."}, status=503)

        receipt = "CG" + uuid.uuid4().hex[:32]
        record = RazorpayPlanPayment.objects.create(
            company=request.user.company, branch=request.user.branch,
            requested_by=request.user, plan=plan,
            cycle=cycle, validity_days=plan.access_duration_days,
            amount_paise=amount_paise, currency="INR", receipt=receipt,
        )
        try:
            remote = create_order(amount_paise, receipt, request.user.company_id)
            if (
                not isinstance(remote, dict) or not str(remote.get("id", "")).startswith("order_")
                or remote.get("currency") != "INR" or remote.get("amount") != amount_paise
            ):
                raise GatewayError("Unexpected response from Razorpay.")
        except GatewayError:
            record.delete()
            return Response({"message": "Unable to create payment order. Try again."}, status=503)

        record.razorpay_order_id = remote["id"]
        record.save(update_fields=["razorpay_order_id", "updated_at"])
        return Response({
            "orderId": record.razorpay_order_id,
            "amount": record.amount_paise, "currency": "INR",
            "keyId": settings.RAZORPAY_KEY_ID,
            "plan": plan.name, "validityDays": record.validity_days,
            "receipt": record.receipt,
            "prefill": {"name": request.user.name or "", "email": request.user.email},
        }, status=201)


class CheckoutVerifyView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [CheckoutVerifyThrottle]

    def post(self, request):
        payload = VerifyInput(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        row = RazorpayPlanPayment.objects.filter(
            company=request.user.company, razorpay_order_id=d["razorpay_order_id"],
        ).first()
        if not row:
            return Response({"message": "Unknown payment order."}, status=404)
        if row.requested_by_id != request.user.id and not user_has_permission(request.user, "billing.edit"):
            return Response({"message": "No access to this payment."}, status=403)
        if not configured() or not verify_checkout(
            row.razorpay_order_id, d["razorpay_payment_id"], d["razorpay_signature"]
        ):
            return Response({"message": "Invalid payment signature."}, status=400)
        try:
            payment = fetch_payment(d["razorpay_payment_id"])
            result = capture_order(row, payment)
        except GatewayError:
            return Response({"message": "Payment is awaiting confirmation. Check your history shortly."}, status=503)
        except (ValueError, IntegrityError):
            return Response({"message": "Payment is not captured or does not match this order."}, status=409)
        return Response({"payment": public_payment(result), "access": access_state(request.user.company)})


class CheckoutPaymentHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not user_has_permission(request.user, "billing.view"):
            return Response({"message": "Billing permission is required."}, status=403)
        qs = RazorpayPlanPayment.objects.filter(company=request.user.company).select_related("plan")
        return Response([public_payment(row) for row in qs[:100]])


class CheckoutOrderStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id):
        record = RazorpayPlanPayment.objects.filter(
            company=request.user.company, razorpay_order_id=order_id,
        ).select_related("plan").first()
        if not record:
            return Response({"message": "Order not found."}, status=404)
        return Response({"payment": public_payment(record), "access": access_state(request.user.company)})


class RazorpayWebhookView(APIView):
    """Raw-body HMAC validation; repeat deliveries must never extend entitlement twice."""
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request):
        raw = request._request.body
        if len(raw) > 262144:
            return Response({"message": "Webhook too large."}, status=413)
        signature = request.headers.get("X-Razorpay-Signature", "")
        delivery_id = request.headers.get("X-Razorpay-Event-Id", "")
        if not verify_webhook(raw, signature) or not delivery_id or len(delivery_id) > 150:
            return Response({"message": "Webhook signature invalid."}, status=400)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return Response({"message": "Invalid webhook payload."}, status=400)
        event_type = str(data.get("event") or "")[:100]
        payload = data.get("payload") or {}
        if not isinstance(payload, dict):
            return Response({"message": "Invalid webhook payload."}, status=400)
        with transaction.atomic():
            if RazorpayWebhookDelivery.objects.filter(event_id=delivery_id).exists():
                return Response({"received": True, "duplicate": True})
            if event_type == "payment.captured":
                entity = ((payload.get("payment") or {}).get("entity") or {})
                row = RazorpayPlanPayment.objects.filter(
                    razorpay_order_id=entity.get("order_id"),
                ).first()
                if row:
                    try:
                        capture_order(row, entity)
                    except ValueError:
                        return Response({"message": "Unmatched captured payment."}, status=400)
            elif event_type == "refund.processed":
                payment = ((payload.get("payment") or {}).get("entity") or {})
                refund = ((payload.get("refund") or {}).get("entity") or {})
                row = RazorpayPlanPayment.objects.filter(
                    razorpay_payment_id=payment.get("id"), status="captured",
                ).first()
                # Only a processed, fully refunded order revokes paid access.
                if (row and refund.get("status") == "processed"
                    and refund.get("payment_id") == row.razorpay_payment_id
                    and int(payment.get("amount_refunded") or 0) >= row.amount_paise):
                    refund_order(row)
            RazorpayWebhookDelivery.objects.create(event_id=delivery_id, event_type=event_type)
        return Response({"received": True})
