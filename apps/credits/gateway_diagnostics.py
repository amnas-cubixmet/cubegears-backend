"""Authenticated, read-only Razorpay test-mode diagnostics for company billing admins.

Never return merchant keys, webhook secrets, upstream response bodies, or auth headers.
The probe performs only a GET; it never creates an order, charge or wallet credit.
"""
from django.conf import settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.accounts.permissions import user_has_permission
from apps.saas.razorpay_client import GatewayError, api_request, configured


class RazorpayTestThrottle(UserRateThrottle):
    rate = "6/hour"


def _mode():
    key_id = settings.RAZORPAY_KEY_ID
    if key_id.startswith("rzp_test_"):
        return "test"
    if key_id.startswith("rzp_live_"):
        return "live"
    return "not_configured" if not key_id else "unrecognized"


def _billing_admin(request):
    return bool(request.user.company_id and user_has_permission(request.user, "billing.edit"))


class GatewayStatusView(APIView):
    """Return safe integration readiness metadata (not an API connectivity test)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _billing_admin(request):
            return Response({"message": "Company billing administrator permission required."}, status=403)
        webhook = settings.RAZORPAY_WEBHOOK_SECRET
        return Response({
            "provider": "razorpay",
            "mode": _mode(),
            "configured": configured(),
            "webhookSecretPresent": bool(webhook and webhook != "your_webhook_secret"),
            "probeAvailable": configured() and _mode() == "test",
        })


class GatewayTestView(APIView):
    """Explicit admin-triggered, read-only GET to Razorpay's test Orders API."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [RazorpayTestThrottle]

    def post(self, request):
        if not _billing_admin(request):
            return Response({"message": "Company billing administrator permission required."}, status=403)
        if _mode() != "test":
            return Response({
                "ok": False, "code": "TEST_MODE_REQUIRED",
                "message": "The connection test only runs with rzp_test_ credentials.",
            }, status=409)
        if not configured():
            return Response({
                "ok": False, "code": "RAZORPAY_NOT_CONFIGURED",
                "message": "Add both Razorpay test credentials to the backend environment.",
            }, status=503)

        try:
            api_request("GET", "orders?count=1")
        except GatewayError as exc:
            code = {
                "invalid_credentials": "RAZORPAY_AUTH_FAILED",
                "network": "RAZORPAY_NETWORK_ERROR",
            }.get(exc.reason, "RAZORPAY_UNAVAILABLE")
            return Response({
                "ok": False, "code": code,
                "message": "Razorpay test API connection failed. Check the backend credentials and network.",
            }, status=503)

        return Response({
            "ok": True, "mode": "test",
            "message": "Razorpay test API authentication succeeded. No order or payment was created.",
        })
