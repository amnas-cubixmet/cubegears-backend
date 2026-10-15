from apps.accounts.permissions import IsCompanyOwner
import json
import os
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import UserRateThrottle
from .models import SubscriptionPlan
from .payment_models import PaymentPolicy, SubscriptionPayment
from .payment_gateway import api_request, verify_checkout, verify_webhook
from .payment_service import activate_captured, has_paid_access

class CheckoutThrottle(UserRateThrottle):
    rate="12/hour"

class PaymentAccessView(APIView):
    permission_classes=[IsCompanyOwner]
    def get(self,request):
        company=request.user.company
        paid=has_paid_access(company)
        from .models import Subscription
        sub=Subscription.objects.filter(company=company,status__iexact="Active",
            renews_at__gt=timezone.now()).order_by("-renews_at").first()
        return Response({"paid":paid,"required":PaymentPolicy.current().enforce_paywall,
            "expiresAt":sub.renews_at.isoformat() if sub else None,
            "plan":sub.plan if sub else None})

class PaymentOrderView(APIView):
    permission_classes=[IsCompanyOwner]
    throttle_classes=[CheckoutThrottle]
    def post(self,request):
        if not request.user.company_id:
            return Response({"message":"Workshop account required."},status=403)
        try: plan_id=int(request.data.get("planId"))
        except (ValueError,TypeError): return Response({"message":"Invalid plan."},status=400)
        cycle=request.data.get("cycle")
        if cycle not in ("monthly","yearly"): return Response({"message":"Invalid billing cycle."},status=400)
        plan=SubscriptionPlan.objects.filter(pk=plan_id,is_public=True,is_active=True).first()
        if not plan: return Response({"message":"Plan unavailable."},status=404)
        amount=int((plan.price_for(cycle)*Decimal("100")).quantize(Decimal("1"),rounding=ROUND_HALF_UP))
        if amount<100: return Response({"message":"Plan price must be at least INR 1."},status=400)
        try:
            gateway=api_request("POST","orders",{"amount":amount,"currency":"INR",
                "receipt":f"cg{str(request.user.company_id).replace('-','')[:10]}{int(timezone.now().timestamp())}",
                "notes":{"company":str(request.user.company_id),"plan":str(plan.id),"cycle":cycle}})
        except RuntimeError:
            return Response({"message":"Payment gateway temporarily unavailable."},status=503)
        if gateway.get("currency")!="INR" or int(gateway.get("amount",0))!=amount or not gateway.get("id"):
            return Response({"message":"Unexpected payment gateway response."},status=502)
        entry=SubscriptionPayment.objects.create(company=request.user.company,
            branch=request.user.branch,requested_by=request.user,plan=plan,
            billing_cycle=cycle,order_id=gateway["id"],amount_paise=amount)
        return Response({"orderId":entry.order_id,"key":os.environ.get("RAZORPAY_KEY_ID",""),
            "amount":amount,"currency":"INR","plan":plan.name},status=201)

class PaymentVerifyView(APIView):
    permission_classes=[IsCompanyOwner]
    throttle_classes=[CheckoutThrottle]
    def post(self,request):
        order_id=str(request.data.get("razorpay_order_id",""))
        payment_id=str(request.data.get("razorpay_payment_id",""))
        signature=str(request.data.get("razorpay_signature",""))
        entry=SubscriptionPayment.objects.filter(company=request.user.company,order_id=order_id).first()
        if not entry or not payment_id or not verify_checkout(order_id,payment_id,signature):
            return Response({"message":"Payment verification failed."},status=400)
        try:
            paid=activate_captured(order_id,payment_id)
        except (ValueError,RuntimeError):
            return Response({"message":"Payment not captured yet. Please check history shortly."},status=409)
        return Response({"status":"captured","accessUntil":paid.access_until.isoformat()})

class PaymentHistoryView(APIView):
    permission_classes=[IsCompanyOwner]
    def get(self,request):
        rows=SubscriptionPayment.objects.filter(company=request.user.company)[:100]
        return Response([{"orderId":r.order_id,"paymentId":r.payment_id,
            "plan":r.plan.name,"cycle":r.billing_cycle,"amount":r.amount_paise/100,
            "currency":r.currency,"status":r.status,
            "paidAt":r.paid_at.isoformat() if r.paid_at else None,
            "accessUntil":r.access_until.isoformat() if r.access_until else None}
            for r in rows.select_related("plan")])

class RazorpayWebhookView(APIView):
    permission_classes=[AllowAny]
    authentication_classes=[]
    throttle_classes=[]
    def post(self,request):
        body=request._request.body
        if not verify_webhook(body,request.headers.get("X-Razorpay-Signature")):
            return Response({"message":"Invalid webhook signature."},status=400)
        try:
            event=json.loads(body)
        except ValueError:
            return Response({"message":"Invalid JSON."},status=400)
        if event.get("event") not in ("payment.captured","order.paid"):
            return Response({"ok":True})
        entity=event.get("payload",{}).get("payment",{}).get("entity",{})
        order_id=entity.get("order_id")
        payment_id=entity.get("id")
        if not order_id or not payment_id:
            return Response({"ok":True})
        if not SubscriptionPayment.objects.filter(order_id=order_id).exists():
            return Response({"ok":True})
        try:
            activate_captured(order_id,payment_id)
        except (RuntimeError,ValueError):
            return Response({"message":"Payment confirmation pending."},status=503)
        return Response({"ok":True})
