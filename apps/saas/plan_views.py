"""Tenant-safe SaaS plan catalog and quote/change request APIs.

Requests never activate subscriptions or change billed amounts. Platform staff
must verify payment and provision subscriptions separately.
"""
from django.db.models import Sum
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.accounts.permissions import RolePermission
from .models import (
    Subscription, SubscriptionPlan, FeatureGroup, PlanFeature,
    PlanFeatureAccess, PlanChangeRequest, CustomPlanRequest, MediaFile,
)


class PlanChangeInput(serializers.Serializer):
    plan_id = serializers.IntegerField(min_value=1)
    billing_cycle = serializers.ChoiceField(choices=("monthly", "yearly"))
    message = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class CustomPlanInput(serializers.Serializer):
    email = serializers.EmailField()
    message = serializers.CharField(min_length=5, max_length=4000)
    branches = serializers.IntegerField(min_value=1, max_value=10000)
    users = serializers.IntegerField(min_value=1, max_value=100000)


def plan_payload(plan):
    """Expose pricing and the complete nested/flat feature selection."""
    feature_access = {
        access.feature_id: access.enabled
        for access in plan.feature_access.all()
    }
    features = [
        {
            "key": feature.key,
            "label": feature.label,
            "description": feature.description,
            "group": feature.group.label,
            "groupKey": feature.group.key,
            "parentGroupKey": feature.group.parent.key if feature.group.parent_id else None,
            "enabled": feature_access.get(feature.id, False),
        }
        for feature in PlanFeature.objects.filter(is_active=True, group__is_active=True)
            .select_related("group__parent").order_by("group__sort_order", "sort_order", "label")
    ]
    return {
        "id": plan.id,
        "key": plan.key,
        "name": plan.name,
        "description": plan.description,
        "monthlyPrice": str(plan.price_for("monthly")),
        "yearlyPrice": str(plan.price_for("yearly")),
        "regularMonthlyPrice": str(plan.monthly_price),
        "regularYearlyPrice": str(plan.yearly_price),
        "promoPercent": plan.promo_percent,
        "maxBranches": plan.max_branches,
        "maxUsers": plan.max_users,
        "includedStorageGb": plan.included_storage_gb,
        "extraStorageRate": str(plan.extra_storage_rate),
        "trialDays": plan.trial_days,
        "accessDurationDays": plan.access_duration_days,
        "checkoutPrice": str(plan.checkout_price),
        "features": features,
    }


class PlanCatalogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        plans = SubscriptionPlan.objects.filter(is_active=True, is_public=True).prefetch_related("feature_access")
        return Response([plan_payload(plan) for plan in plans])


class PlanAccessView(APIView):
    permission_classes = [RolePermission]
    permission_code = "billing.view"

    def get(self, request):
        company = request.user.company
        subscription = (
            Subscription.objects.filter(company=company, status__iexact="Active")
            .select_related("plan_definition").order_by("-created_at").first()
        )
        if not subscription or not subscription.plan_definition or not subscription.plan_definition.is_active:
            return Response({"configured": False, "features": {}, "limits": {}})
        plan = subscription.plan_definition
        feature_map = {
            item.feature.key: item.enabled
            for item in plan.feature_access.select_related("feature").all()
        }
        # Invoices/tax-compliant billing are available in every plan.
        feature_map["invoices"] = True
        return Response({
            "configured": True,
            "plan": plan.key,
            "billingCycle": subscription.billing_cycle,
            "features": feature_map,
            "limits": {"branches": plan.max_branches, "users": plan.max_users,
                       "storageGb": plan.included_storage_gb},
        })


class PlanChangeRequestView(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": "billing.view", "POST": "billing.edit"}

    def get(self, request):
        rows = PlanChangeRequest.objects.filter(company=request.user.company).select_related("requested_plan")[:30]
        return Response([
            {"id": row.id, "plan": row.requested_plan.name, "billingCycle": row.billing_cycle,
             "status": row.status, "createdAt": row.created_at.isoformat()}
            for row in rows
        ])

    def post(self, request):
        serializer = PlanChangeInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            plan = SubscriptionPlan.objects.get(
                pk=serializer.validated_data["plan_id"], is_active=True, is_public=True,
            )
        except SubscriptionPlan.DoesNotExist:
            return Response({"message": "This plan is not available."}, status=404)
        company = request.user.company
        current = Subscription.objects.filter(
            company=company, status__iexact="Active"
        ).order_by("-created_at").first()
        if current and current.plan_definition_id == plan.id and current.billing_cycle == serializer.validated_data["billing_cycle"]:
            return Response({"message": "This is already your current plan."}, status=400)
        if PlanChangeRequest.objects.filter(company=company, requested_plan=plan, status="pending").exists():
            return Response({"message": "A request for this plan is already pending."}, status=409)
        row = PlanChangeRequest.objects.create(
            company=company, requested_by=request.user, requested_plan=plan,
            billing_cycle=serializer.validated_data["billing_cycle"],
            message=serializer.validated_data.get("message", ""),
        )
        return Response({"id": row.id, "status": row.status,
                         "message": "Request received. CubixGear will contact you before any change or charge."},
                        status=status.HTTP_201_CREATED)


class CustomPlanRequestView(APIView):
    permission_classes = [RolePermission]
    permission_map = {"GET": "billing.view", "POST": "billing.edit"}

    def get(self, request):
        rows = CustomPlanRequest.objects.filter(company=request.user.company)[:30]
        return Response([
            {"id": r.id, "branches": r.branches, "users": r.users,
             "status": r.status, "createdAt": r.created_at.isoformat()}
            for r in rows
        ])

    def post(self, request):
        serializer = CustomPlanInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = CustomPlanRequest.objects.create(
            company=request.user.company, requested_by=request.user,
            **serializer.validated_data,
        )
        return Response({"id": row.id, "status": row.status,
                         "message": "Custom plan enquiry received. Our team will contact you."},
                        status=status.HTTP_201_CREATED)
