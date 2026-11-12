from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import User
from .phone import normalize_phone


class CompanyMiniSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()
    currency = serializers.CharField()
    plan = serializers.CharField()


class BranchMiniSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    code = serializers.CharField()


class RoleMiniSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    code = serializers.CharField()
    permissions = serializers.ListField(child=serializers.CharField())


class UserSerializer(serializers.ModelSerializer):
    company = CompanyMiniSerializer(read_only=True)
    branch = BranchMiniSerializer(read_only=True)
    role = RoleMiniSerializer(read_only=True)
    isCompanyOwner = serializers.SerializerMethodField()
    paidAccessRequired = serializers.SerializerMethodField()
    onboardingRequired = serializers.SerializerMethodField()
    effectivePermissions = serializers.SerializerMethodField()

    def get_isCompanyOwner(self, obj):
        from .permissions import is_company_owner
        return is_company_owner(obj)

    def get_onboardingRequired(self, obj):
        # Legacy tenants have no wizard state: never force a migration-time
        # redirect or block existing owner logins.
        if not obj.company_id or obj.company.owner_id != obj.pk:
            return False
        from apps.saas.models import CompanySetting
        state = CompanySetting.objects.filter(
            company_id=obj.company_id, category="onboarding",
        ).values_list("data", flat=True).first() or {}
        return bool(state.get("required") and not state.get("completed"))

    def get_paidAccessRequired(self, obj):
        return bool(obj.company_id and obj.company.require_paid_features)

    employeeProfileId = serializers.SerializerMethodField()
    employeeDisplayNo = serializers.SerializerMethodField()

    def get_effectivePermissions(self, obj):
        from apps.accounts.permissions import user_has_permission
        from apps.roles.permission_catalog import TENANT_PERMISSIONS, PANEL_PERMISSIONS
        return [code for code in TENANT_PERMISSIONS + PANEL_PERMISSIONS if user_has_permission(obj, code)]

    def _employee(self, obj):
        try:
            return obj.employee_profile
        except Exception:
            return None

    def get_employeeProfileId(self, obj):
        employee = self._employee(obj)
        return str(employee.id) if employee else None

    def get_employeeDisplayNo(self, obj):
        employee = self._employee(obj)
        if not employee:
            return None
        digits = "".join(ch for ch in str(employee.employee_code or "") if ch.isdigit())
        return f"Employee {int(digits)}" if digits else "Employee"

    class Meta:
        model = User
        fields = [
            "id", "name", "email", "phone", "normalized_phone", "avatar",
            "company", "branch", "role",
            "employeeProfileId", "employeeDisplayNo", "effectivePermissions", "isCompanyOwner", "paidAccessRequired", "onboardingRequired",
            "is_staff", "is_superuser", "email_verified",
            "created_at",
        ]


def user_can_sign_in(user, *, allow_pending_setup=False):
    """All login methods must enforce the same active tenant and role checks."""
    if not user or not user.is_active:
        return False
    # Pending password setup is not a valid API session, even for legacy
    # workshop records with no Employee profile.
    if not allow_pending_setup and not user.has_usable_password():
        return False
    if not user.company_id:
        return True
    role = user.role
    try:
        employee = user.employee_profile
    except Exception:
        employee = None
    # A profile edited to Suspended/Inactive must not keep working JWT-login
    # credentials, even if a legacy code path omitted syncing is_active.
    if employee and employee.status not in {"Active", "Probation", "Notice Period"}:
        return False
    # Password-setup links use a separate, one-time authorization check.
    # A pending staff identity must never sign in via older magic-link paths.
    if employee and not user.has_usable_password() and not allow_pending_setup:
        return False
    return bool(
        user.company and user.company.is_active
        and role and role.is_active and role.company_id == user.company_id
    )


class LoginSerializer(serializers.Serializer):
    """Email or international mobile + password, preserving old email clients."""
    identifier = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    mobile = serializers.CharField(required=False, allow_blank=True)
    workshop = serializers.SlugField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        from django.contrib.auth.hashers import check_password
        ident = str(attrs.get("identifier") or attrs.get("email") or attrs.get("mobile") or "").strip()
        password = attrs["password"]
        generic = "Invalid credentials or workshop selection."
        if not ident:
            raise serializers.ValidationError({"identifier":"Enter your email or mobile number."})
        if "@" in ident:
            # Preserve stored email casing when calling Django's auth
            # backend. Legacy records may have the same email in different
            # case across workshops; never silently select one account.
            candidates = User.objects.filter(email__iexact=ident).select_related(
                "company", "role"
            ).order_by("id")
            company_slug = str(attrs.get("workshop") or "").strip().lower()
            if company_slug:
                candidates = candidates.filter(company__slug=company_slug)
            email_matches = list(candidates[:6])
            if not company_slug and len(email_matches) > 1:
                # Reveal the workshop requirement only after a valid
                # password, as with ambiguous mobile identities.
                if any(row.check_password(password) and user_can_sign_in(row)
                       for row in email_matches):
                    raise serializers.ValidationError({
                        "workshop": "Enter your workshop code for this email address."
                    })
                check_password(password, "!" + ("invalid" * 14))
                raise serializers.ValidationError(generic)
            if len(email_matches) != 1:
                raise serializers.ValidationError(generic)
            user = authenticate(
                request=self.context.get("request"),
                email=email_matches[0].email, password=password,
            )
            if not user or not user_can_sign_in(user):
                raise serializers.ValidationError(generic)
        else:
            try:
                mobile = normalize_phone(ident)
            except Exception:
                # Similar response for nonexistent and badly formatted numbers.
                raise serializers.ValidationError(generic)
            candidates = User.objects.filter(normalized_phone=mobile).select_related(
                "company", "role"
            ).order_by("id")
            company_slug = str(attrs.get("workshop") or "").strip().lower()
            if company_slug:
                candidates = candidates.filter(company__slug=company_slug)
            # Never silently choose among two workshop accounts sharing a phone.
            # Require a workshop code only after a valid password is supplied.
            matches = list(candidates[:6])
            if not company_slug and len(matches) > 1:
                if any(row.check_password(password) and user_can_sign_in(row) for row in matches):
                    raise serializers.ValidationError({"workshop":"Enter your workshop code for this mobile number."})
                check_password(password, "!" + ("invalid" * 14))
                raise serializers.ValidationError(generic)
            user = next((row for row in matches if row.check_password(password) and user_can_sign_in(row)), None)
            if not user:
                # Dummy hash verification is intentionally symmetrical for absent users.
                check_password(password, "!" + ("invalid" * 14))
                raise serializers.ValidationError(generic)
        attrs["user"] = user
        return attrs


class ForgotPasswordSerializer(serializers.Serializer):
    # Mobile recovery is workshop-scoped and never requires an email address.
    # Keep the legacy email path for existing owner/platform accounts.
    email = serializers.EmailField(required=False, allow_blank=True)
    mobile = serializers.CharField(required=False, allow_blank=True)
    workshop = serializers.SlugField(required=False, allow_blank=True)

    def validate(self, attrs):
        mobile = str(attrs.get("mobile") or "").strip()
        email = str(attrs.get("email") or "").strip()
        if mobile:
            try:
                attrs["mobile"] = normalize_phone(mobile)
            except Exception:
                raise serializers.ValidationError({
                    "mobile": "Use an international mobile number, e.g. +919876543210."
                })
            if not attrs.get("workshop"):
                raise serializers.ValidationError({
                    "workshop": "Enter your workshop code to request a secure WhatsApp reset."
                })
        elif not email:
            raise serializers.ValidationError({
                "mobile": "Enter your mobile number with country code."
            })
        return attrs


class ResetPasswordSerializer(serializers.Serializer):
    uid = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    token = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if not attrs.get("uid") and not attrs.get("email"):
            raise serializers.ValidationError("uid or email is required.")
        return attrs

    def validate_password(self, value):
        validate_password(value)
        return value


class MagicLinkRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class MagicLinkVerifySerializer(serializers.Serializer):
    token = serializers.CharField()


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, required=False)
    new_password = serializers.CharField(write_only=True, required=False)
    currentPassword = serializers.CharField(write_only=True, required=False)
    newPassword = serializers.CharField(write_only=True, required=False)

    def validate(self, attrs):
        current = attrs.get("current_password") or attrs.get("currentPassword")
        new = attrs.get("new_password") or attrs.get("newPassword")
        if not current or not new:
            raise serializers.ValidationError("Current password and new password are required.")
        validate_password(new)
        attrs["current_password"] = current
        attrs["new_password"] = new
        return attrs


class PublicWorkshopSignupSerializer(serializers.Serializer):
    workshop_name = serializers.CharField(max_length=180)
    owner_name = serializers.CharField(max_length=150)
    mobile = serializers.CharField(max_length=30)
    email = serializers.EmailField(required=False, allow_blank=True)
    whatsapp_consent = serializers.BooleanField(required=False, default=False)
    # An owner can securely choose a password at registration without waiting
    # for a WhatsApp or email delivery channel. Legacy invitation signup remains.
    password = serializers.CharField(required=False, write_only=True, trim_whitespace=False, max_length=128)
    confirm_password = serializers.CharField(required=False, write_only=True, trim_whitespace=False, max_length=128)

    def validate(self, attrs):
        password = attrs.get("password")
        confirmation = attrs.pop("confirm_password", None)
        if password:
            if password != confirmation:
                raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
            validate_password(password)
        elif confirmation:
            raise serializers.ValidationError({"password": "Enter a password."})
        elif not attrs.get("email"):
            # Cloud WhatsApp owner delivery has been retired. A public
            # anonymous response must never expose a password bearer token.
            raise serializers.ValidationError({
                "email": "Provide an email for password setup, or choose a password during signup."
            })
        return attrs

    def validate_mobile(self, value):
        mobile = normalize_phone(value)
        # New workshop owners must not register an already-used contact number.
        # Scope this policy to public signup: existing multi-workshop staff
        # mobile logins and their workshop-code selection remain unchanged.
        from apps.companies.models import Company
        if (User.objects.filter(normalized_phone=mobile).exists()
                or Company.objects.filter(phone=mobile).exists()):
            raise serializers.ValidationError(
                "This mobile number is already registered. Please sign in or use another number."
            )
        return mobile

    def validate_email(self, value):
        value = value.lower().strip()
        if value and User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value
