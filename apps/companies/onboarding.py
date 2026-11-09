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
from apps.inventory.models import WorkshopFinancialAccount

STEPS = ("company", "branch", "operations", "bank", "billing")
# Existing 4-step workshops must remain completed; bank is optional for new
# signups, but owners explicitly confirm skipping before finalizing setup.
LEGACY_STEPS = ("company", "branch", "operations", "billing")
DEFAULT_OPERATIONS = {"workingStart": "09:00", "workingEnd": "18:30",
                      "bookingSlotMinutes": 30, "jobPrefix": "JOB-"}
DEFAULT_BILLING = {"invoicePrefix": "INV-", "paymentTermsDays": 0,
                   "defaultTaxRate": 18, "rounding": True}

# Required for NEW signups only. GSTIN requires an explicit opt-out if
# unregistered; email, GPS and logo can be omitted for mobile-only workshops.
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
    state = setting_data(company, "onboarding")
    if not state.get("gstNotRegistered") and not company.gstin:
        errors["company"]["gstin"] = ["Enter a GSTIN or confirm no GST registration."]
    if state.get("gstNotRegistered") and company.gstin:
        errors["company"]["gstin"] = ["The GST registration choice does not match saved GSTIN."]
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
    gstNotRegistered = serializers.BooleanField(required=False, default=False)

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


class BankSetupForm(serializers.Serializer):
    mode = serializers.ChoiceField(choices=("new", "existing", "skip"))
    accountId = serializers.UUIDField(required=False)
    name = serializers.CharField(required=False, max_length=120, allow_blank=True)
    bankName = serializers.CharField(required=False, max_length=120, allow_blank=True)
    accountNumber = serializers.CharField(required=False, max_length=50, allow_blank=True)
    ifsc = serializers.CharField(required=False, max_length=20, allow_blank=True)
    branchName = serializers.CharField(required=False, max_length=120, allow_blank=True)
    openingBalance = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False,
        min_value=Decimal("0.00"),
    )
    openingDate = serializers.DateField(required=False, allow_null=True)

    def validate(self, values):
        mode = values["mode"]
        if mode == "existing":
            if not values.get("accountId"):
                raise serializers.ValidationError({"accountId": "Choose an existing bank account."})
        elif mode == "new":
            for field in ("name", "bankName"):
                if not str(values.get(field) or "").strip():
                    raise serializers.ValidationError({field: "This bank detail is required."})
            if not values.get("accountNumber"):
                # The full account number is optional; it must never appear in
                # onboarding progress JSON or public account summaries.
                values["accountNumber"] = ""
        return values


FORMS = {"company": CompanySetupForm, "branch": BranchSetupForm,
         "operations": OperationsSetupForm, "bank": BankSetupForm,
         "billing": BillingSetupForm}


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
    completed_steps = state.get("completedSteps", [])
    done = [step for step in STEPS if step in completed_steps or (
        # Old fully completed workshops had only four setup steps.
        step == "bank" and state.get("completed")
        and all(old in completed_steps for old in LEGACY_STEPS)
    )]
    location = setting_data(company, "attendance")
    operations = setting_data(company, "operations", DEFAULT_OPERATIONS)
    billing = setting_data(company, "billing", DEFAULT_BILLING)
    fields = ("name", "legal_name", "phone", "email", "address",
              "city", "state", "country", "pincode", "gstin")
    branch_fields = ("name", "phone", "address", "city", "state", "pincode")
    from apps.credits.models import CreditWallet
    from apps.finance.views import mask_account_number
    wallet = CreditWallet.objects.filter(company=company).values_list(
        "balance", flat=True,
    ).first()

    existing_banks = list(WorkshopFinancialAccount.objects.filter(
        company=company, kind="Bank",
    ).order_by("name", "pk"))
    bank_by_id = {str(account.pk): account for account in existing_banks}
    selected_bank = bank_by_id.get(str(state.get("bankAccountId") or ""))
    bank_accounts = [{
        "id": str(account.pk),
        "name": account.name,
        "bankName": account.bank_name,
        "accountNumberMasked": mask_account_number(account.account_number),
        "openingBalance": str(account.opening_balance),
    } for account in existing_banks]
    return {
        "required": bool(state.get("required", False)),
        "completed": bool(state.get("completed") and len(done) == len(STEPS)),
        "completedSteps": done,
        "steps": [{"key": step, "completed": step in done} for step in STEPS],
        "company": {
            **{key: getattr(company, key) for key in fields},
            "gstNotRegistered": bool(state.get("gstNotRegistered", False)),
        },
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
        "bank": {
            "configured": selected_bank is not None,
            "skipped": bool(state.get("bankSkipped", False)) and selected_bank is None,
            "accountId": str(selected_bank.pk) if selected_bank else "",
            "name": selected_bank.name if selected_bank else "",
            "bankName": selected_bank.bank_name if selected_bank else "",
            "accountNumberMasked": mask_account_number(selected_bank.account_number)
            if selected_bank else "",
            "ifsc": selected_bank.ifsc if selected_bank else "",
            "branchName": selected_bank.branch_name if selected_bank else "",
            "openingBalance": str(selected_bank.opening_balance) if selected_bank else "0.00",
            "openingDate": selected_bank.opening_date.isoformat()
            if selected_bank and selected_bank.opening_date else "",
            "availableAccounts": bank_accounts,
        },
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
        pending = onboarding_pending(company)
        if pending:
            errors = required_field_errors(step, values)
            if step == "company":
                if values.get("gstNotRegistered") and values.get("gstin"):
                    errors["gstin"] = ["Clear GSTIN when confirming no GST registration."]
                elif not values.get("gstNotRegistered") and not values.get("gstin"):
                    errors["gstin"] = ["Provide GSTIN or tick No GST registration."]
            if errors:
                return Response(errors, status=400)

        if step == "company":
            gst_not_registered = values.pop("gstNotRegistered", False)
            if pending and gst_not_registered:
                values["gstin"] = ""
            for key, value in values.items():
                setattr(company, key, value)
            company.save(update_fields=[*values, "updated_at"])
            if pending:
                update_setting(company, branch, "onboarding", {
                    "gstNotRegistered": bool(gst_not_registered),
                })
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
        elif step == "bank":
            mode = values["mode"]
            setup_state = setting_data(company, "onboarding")
            selected_id = setup_state.get("bankAccountId")
            if mode == "skip":
                # Never delete an existing account or its immutable ledger.
                update_setting(company, branch, "onboarding", {
                    "bankSkipped": not bool(selected_id),
                })
            elif mode == "existing":
                selected = WorkshopFinancialAccount.objects.filter(
                    company=company, kind="Bank", pk=values["accountId"],
                ).first()
                if not selected:
                    return Response(
                        {"accountId": ["Choose a bank account belonging to this workshop."]},
                        status=400,
                    )
                if selected_id and str(selected.pk) != str(selected_id):
                    return Response(
                        {"accountId": ["A bank is already configured. Manage accounts in Finance."]},
                        status=400,
                    )
                update_setting(company, branch, "onboarding", {
                    "bankAccountId": str(selected.pk), "bankSkipped": False,
                })
            else:
                name = values["name"].strip()
                bank_name = values["bankName"].strip()
                account_number = values.get("accountNumber", "").strip()
                opening = values.get("openingBalance", Decimal("0.00"))
                if selected_id:
                    existing = WorkshopFinancialAccount.objects.filter(
                        company=company, kind="Bank", pk=selected_id,
                    ).first()
                    # The unique onboarding account ID prevents duplicate
                    # creation on retry. Do not revise a posted opening balance.
                    if not existing or (
                        existing.name != name or existing.bank_name != bank_name or
                        existing.opening_balance != opening or
                        (account_number and existing.account_number != account_number)
                    ):
                        return Response({
                            "bank": ["Bank setup is already saved. Manage changes in Finance > Accounts."]
                        }, status=400)
                else:
                    if WorkshopFinancialAccount.objects.filter(
                        company=company, name__iexact=name,
                    ).exists():
                        return Response({
                            "name": ["This account already exists. Select it from existing banks."]
                        }, status=400)
                    existing = WorkshopFinancialAccount.objects.create(
                        company=company, branch=branch,
                        name=name, kind="Bank", bank_name=bank_name,
                        account_number=account_number,
                        ifsc=values.get("ifsc", "").upper().strip(),
                        branch_name=values.get("branchName", "").strip(),
                        opening_balance=opening,
                        opening_date=values.get("openingDate"),
                        created_by=request.user, is_active=True,
                    )
                    # WorkshopFinancialAccount post_save posts the balanced
                    # opening journal once using the account's immutable ID.
                update_setting(company, branch, "onboarding", {
                    "bankAccountId": str(existing.pk), "bankSkipped": False,
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
        completed = state.get("completedSteps", [])
        missing = [step for step in STEPS if step not in completed and not (
            step == "bank" and state.get("completed")
            and all(old in completed for old in LEGACY_STEPS)
        )]
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
