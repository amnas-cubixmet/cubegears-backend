"""Authenticated credit-package checkout using Razorpay's hosted Payment Links."""
import uuid
from decimal import Decimal
from urllib.parse import urlparse

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.accounts.permissions import user_has_permission, IsCompanyOwner
from apps.saas.razorpay_client import GatewayError, api_request, configured
from .hosted_checkout import reconcile_hosted_purchase
from .models import CreditPackage, CreditPurchase


class HostedLinkThrottle(UserRateThrottle):
    rate = "10/hour"


class HostedStatusThrottle(UserRateThrottle):
    rate = "60/hour"


class HostedLinkInput(serializers.Serializer):
    packageId = serializers.IntegerField(min_value=1)


def _admin(user):
    return bool(user.company_id and user_has_permission(user, "billing.edit"))


def _safe_hosted_url(url):
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in {"rzp.io", "payments.razorpay.com"}


class HostedCreditCheckoutView(APIView):
    permission_classes = [IsCompanyOwner]
    throttle_classes = [HostedLinkThrottle]

    def post(self, request):
        if not _admin(request.user):
            return Response({"message": "Company billing administrator permission required."}, status=403)
        payload = HostedLinkInput(data=request.data)
        payload.is_valid(raise_exception=True)
        package = get_object_or_404(
            CreditPackage, pk=payload.validated_data["packageId"], is_active=True,
        )
        amount = int((package.price_inr * Decimal("100")).to_integral_exact())
        if package.credits <= 0 or amount < 100 or amount > 10000000000:
            return Response({"message": "Invalid published credit package."}, status=400)
        if not configured():
            return Response({"message": "Payment gateway is not configured."}, status=503)

        receipt = "CL" + uuid.uuid4().hex[:32]
        purchase = CreditPurchase.objects.create(
            company=request.user.company, package=package,
            requested_by=request.user, credits=package.credits,
            amount_paise=amount, receipt=receipt,
        )
        callback = (
            settings.FRONTEND_URL.rstrip("/")
            + "/account/credits/buy?credit_purchase=" + str(purchase.pk)
        )
        try:
            remote = api_request("POST", "payment_links", {
                "amount": amount,
                "currency": "INR",
                "accept_partial": False,
                "reference_id": receipt,
                "description": "CubeGear credit package: " + package.name[:90],
                "customer": {
                    "name": request.user.name or "Workshop admin",
                    "email": request.user.email,
                },
                "notify": {"sms": False, "email": False},
                "callback_url": callback,
                "callback_method": "get",
                "notes": {
                    "company_id": str(request.user.company_id),
                    "credit_package_id": str(package.pk),
                },
            })
            if (
                not isinstance(remote, dict)
                or not str(remote.get("id") or "").startswith("plink_")
                or remote.get("reference_id") != receipt
                or remote.get("amount") != amount
                or remote.get("currency") != "INR"
                or not _safe_hosted_url(remote.get("short_url"))
            ):
                raise GatewayError("Unexpected payment link.")
        except GatewayError:
            purchase.delete()
            return Response({
                "message": "Could not open Razorpay payment page. Please try again.",
            }, status=503)

        purchase.payment_link_id = remote["id"]
        purchase.checkout_url = remote["short_url"]
        purchase.save(update_fields=["payment_link_id", "checkout_url"])
        return Response({
            "purchaseId": str(purchase.pk),
            "checkoutUrl": purchase.checkout_url,
            "amount": purchase.amount_paise,
            "currency": "INR",
            "credits": str(purchase.credits),
        }, status=201)


class HostedCreditStatusView(APIView):
    permission_classes = [IsCompanyOwner]
    throttle_classes = [HostedStatusThrottle]

    def get(self, request, purchase_id):
        if not request.user.company_id:
            return Response({"message": "Workshop account required."}, status=403)
        purchase = get_object_or_404(
            CreditPurchase, pk=purchase_id, company=request.user.company,
            payment_link_id__isnull=False,
        )
        if purchase.requested_by_id != request.user.pk and not _admin(request.user):
            return Response({"message": "Billing administrator permission required."}, status=403)
        if purchase.status == "pending":
            try:
                purchase = reconcile_hosted_purchase(purchase)
            except GatewayError:
                return Response({
                    "status": "pending", "credited": False,
                    "message": "Razorpay confirmation is pending. Refresh shortly.",
                })
            except ValueError:
                return Response({
                    "status": "pending", "credited": False,
                    "message": "Payment is not verified yet. No credits were added.",
                })
        return Response({
            "status": purchase.status,
            "credited": purchase.status == "credited",
            "credits": str(purchase.credits) if purchase.status == "credited" else "0",
        })
