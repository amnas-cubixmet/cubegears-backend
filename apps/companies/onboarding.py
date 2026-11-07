"""Persistent organization setup backed by the existing tenant-owned models.

No shadow GST, branch, finance, or company master is created.
"""

import re
from decimal import Decimal

from django.db import transaction
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import is_company_owner
from rest_framework.permissions import BasePermission


class IsVerifiedWorkshopOwner(BasePermission):
    """Paid entitlements do not block setup; staff permissions cannot impersonate owner."""

    def has_permission(self, request, view):
        return is_company_owner(request.user)
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.saas.models import CompanySetting

STEPS = ("company", "branch", "operations", "billing")
DEFAULT_OPERATIONS = {"workingStart": "09:00", "workingEnd": "18:30",
                      "bookingSlotMinutes": 30, "jobPrefix": "JOB-"}
DEFAULT_BILLING = {"invoicePrefix": "INV-", "paymentTermsDays": 0,
                   "defaultTaxRate": 18, "rounding": True}

# Required for NEW signups only. GSTIN, email, GPS and logo remain optional:
# some workshops are unregistered and some sign up by WhatsApp/mobile.
REQUIRED_COMPANY_FIELDS = ("name", "legal_name", "phone", "address", "city", "state", "country", "pincode")
REQUIRED_BRANCH_FIELDS = ("name", "phone", "address", "city", "state", "pincode")


def onboarding_pending(company):
    """Legacy tenants without onboarding state are never locked retroactively."""
    if not company:
        return False
    record = CompanySetting.objects.filter(
        company=company, category="onboarding"
    ).values_list("data", flat=True).first() or {}
    return bool(record.get("required") and not record.get("completed"))


def required_field_errors(step, values):
    required = (REQUIRED_COMPANY_FIELDS if step == "company"
                else REQUIRED_BRANCH_FIELDS if step == "branch" else ())
    return {key: ["This workshop detail is required before continuing."]
            for key in required if not str(values.get(key) or "").strip()}


def persisted_setup_errors(company):
    """Never mark signup complete solely because four progress flags were saved."""
    branch = head_branch(company)
    errors = {"company": required_field_errors("company", vars(company)),
              "branch": required_field_errors("branch", vars(branch) if branch else {})}
    return {step: fields for step, fields in errors.items() if fields}


class CompanySetupForm(serializers.Serializer):
    name = serializers.CharField(max_length=180)
    legal_name = serializers.CharField(required=False, allow_blank=True, max_length=220)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=30)
    address = serializers.CharField(required=False, allow_blank=True, max_length=1200)
    city = serializers.CharField(required=False, allow_blank=True, max_length=100)
    state = serializers.CharField(required=False, allow_blank=True, max_length=100)
    country = serializers.CharField(required=False, allow_blank=True, max_length=100)
    pincode = serializers.CharField(required=False, allow_blank=True, max_length=12)
    gstin = serializers.CharField(required=False, allow_blank=True, max_length=15)

    def validate_gstin(self, value):
        value = value.upper().strip()
        if value and not re.fullmatch(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]", value):
            raise serializers.ValidationError("Enter a valid 15-character GSTIN.")
        return value


class BranchSetupForm(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=30)
    address = serializers.CharField(required=False, allow_blank=True, max_length=1200)
    city = serializers.CharField(required=False, allow_blank=True, max_length=100)
    state = serializers.CharField(max_length=100)
    pincode = serializers.CharField(required=False, allow_blank=True, max_length=12)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False,
                                        allow_null=True, min_value=Decimal("-90"), max_value=Decimal("90"))
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False,
                                         allow_null=True, min_value=Decimal("-180"), max_value=Decimal("180"))
    punchRadiusMeters = serializers.IntegerField(required=False, min_value=20, max_value=5000)

    def validate(self, values):
        if (values.get("latitude") is None) != (values.get("longitude") is None):
            raise serializers.ValidationError({"latitude": "Provide both latitude and longitude."})
        return values


class OperationsSetupForm(serializers.Serializer):
    workingStart = serializers.TimeField(input_formats=["%H:%M"])
    workingEnd = serializers.TimeField(input_formats=["%H:%M"])
    bookingSlotMinutes = serializers.IntegerField(min_value=5, max_value=240)
    jobPrefix = serializers.RegexField(r"^[A-Z0-9_-]{1,12}$", max_length=12)

    def validate(self, values):
        if values["workingEnd"] <= values["workingStart"]:
            raise serializers.ValidationError({"workingEnd": "End must follow start."})
        return values


class BillingSetupForm(serializers.Serializer):
    invoicePrefix = serializers.RegexField(r"^[A-Z0-9_-]{1,12}$", max_length=12)
    paymentTermsDays = serializers.IntegerField(min_value=0, max_value=365)
    defaultTaxRate = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100)
    rounding = serializers.BooleanField()


FORMS = {"company": CompanySetupForm, "branch": BranchSetupForm,
         "operations": OperationsSetupForm, "billing": BillingSetupForm}


def setting_data(company, category, defaults=None):
    value = CompanySetting.objects.filter(company=company, category=category).values_list(
        "data", flat=True,
    ).first() or {}
    return {**(defaults or {}), **value}


def update_setting(company, branch, category, changes):
    record, _ = CompanySetting.objects.select_for_update().get_or_create(
        company=company, category=category,
        defaults={"branch": branch, "data": {}},
    )
    record.data = {**(record.data or {}), **changes}
    record.save(update_fields=["data", "updated_at"])
    return record.data


def head_branch(company):
    return (Branch.objects.filter(company=company, is_head_office=True).order_by("created_at").first()
            or Branch.objects.filter(company=company).order_by("created_at").first())


def snapshot(company):
    branch = head_branch(company)
    state = setting_data(company, "onboarding")
    done = [step for step in STEPS if step in state.get("completedSteps", [])]
    location = setting_data(company, "attendance")
    operations = setting_data(company, "operations", DEFAULT_OPERATIONS)
    billing = setting_data(company, "billing", DEFAULT_BILLING)
    fields = ("name", "legal_name", "phone", "email", "address",
              "city", "state", "country", "pincode", "gstin")
    branch_fields = ("name", "phone", "address", "city", "state", "pincode")
    from apps.credits.models import CreditWallet
    wallet = CreditWallet.objects.filter(company=company).values_list(
        "balance", flat=True,
    ).first()
    return {
        "required": bool(state.get("required", False)),
        "completed": bool(state.get("completed") and len(done) == len(STEPS)),
        "completedSteps": done,
        "steps": [{"key": step, "completed": step in done} for step in STEPS],
        "company": {key: getattr(company, key) for key in fields},
        "branch": {
            **({key: getattr(branch, key) for key in branch_fields} if branch else
               {"name": "Head Office", "phone": "", "address": "",
                "city": "", "state": "", "pincode": ""}),
            "latitude": location.get("workshopLatitude"),
            "longitude": location.get("workshopLongitude"),
            "punchRadiusMeters": location.get("punchRadiusMeters", 100),
        },
        "operations": {key: operations[key] for key in DEFAULT_OPERATIONS},
        "billing": {key: billing[key] for key in DEFAULT_BILLING},
        "logoAvailable": bool(company.logo_image),
        "account": {
            "companyName": company.name,
            "companySlug": company.slug,
            "ownerName": company.owner.name if company.owner_id else "",
            "contactPhone": company.phone,
            "contactEmail": company.email,
            "walletBalanceCredits": str(wallet) if wallet is not None else None,
            "walletAvailable": wallet is not None,
            "walletPath": "/account",
            "companySettingsPath": "/account/company",
        },
    }


class WorkshopSetupView(APIView):
    permission_classes = [IsVerifiedWorkshopOwner]

    def get(self, request):
        return Response(snapshot(request.user.company))

    @transaction.atomic
    def patch(self, request):
        step, payload = request.data.get("step"), request.data.get("data")
        if step not in STEPS:
            return Response({"step": ["Unknown setup step."]}, status=400)
        if not isinstance(payload, dict):
            return Response({"data": ["A step object is required."]}, status=400)
        form = FORMS[step](data=payload)
        form.is_valid(raise_exception=True)
        values = form.validated_data
        company = Company.objects.select_for_update().get(pk=request.user.company_id)
        branch = head_branch(company)
        if onboarding_pending(company):
            errors = required_field_errors(step, values)
            if errors:
                return Response(errors, status=400)

        if step == "company":
            for key, value in values.items():
                setattr(company, key, value)
            company.save(update_fields=[*values, "updated_at"])
        elif step == "branch":
            if branch is None:
                from apps.saas.access import enforce_branch_limit
                enforce_branch_limit(company)
                branch = Branch.objects.create(company=company, name="Head Office",
                                               code="HO", is_head_office=True)
            fields = {key: value for key, value in values.items()
                      if key not in ("latitude", "longitude", "punchRadiusMeters")}
            for key, value in fields.items():
                setattr(branch, key, value)
            branch.save(update_fields=list(fields))
            location = {}
            if "latitude" in values and "longitude" in values:
                location = {
                    "workshopLatitude": (str(values["latitude"]) if values["latitude"] is not None else None),
                    "workshopLongitude": (str(values["longitude"]) if values["longitude"] is not None else None),
                }
            if "punchRadiusMeters" in values:
                location["punchRadiusMeters"] = values["punchRadiusMeters"]
            if location:
                update_setting(company, branch, "attendance", location)
        elif step == "operations":
            update_setting(company, branch, "operations", {
                **values, "workingStart": values["workingStart"].strftime("%H:%M"),
                "workingEnd": values["workingEnd"].strftime("%H:%M"),
            })
        elif step == "billing":
            update_setting(company, branch, "billing", {
                **values, "defaultTaxRate": float(values["defaultTaxRate"]),
            })
        state = setting_data(company, "onboarding")
        state["completedSteps"] = [
            key for key in STEPS if key == step or key in state.get("completedSteps", [])
        ]
        update_setting(company, branch, "onboarding", state)
        return Response(snapshot(company))


class WorkshopSetupCompleteView(APIView):
    permission_classes = [IsVerifiedWorkshopOwner]

    @transaction.atomic
    def post(self, request):
        company = Company.objects.select_for_update().get(pk=request.user.company_id)
        state = setting_data(company, "onboarding")
        missing = [step for step in STEPS if step not in state.get("completedSteps", [])]
        if missing:
            return Response({"message": "Save all setup steps first.", "missingSteps": missing}, status=400)
        if state.get("required"):
            errors = persisted_setup_errors(company)
            if errors:
                return Response({
                    "message": "Required workshop information is incomplete.",
                    "missingFields": errors,
                }, status=400)
        state["completed"] = True
        update_setting(company, head_branch(company), "onboarding", state)
        return Response(snapshot(company))
