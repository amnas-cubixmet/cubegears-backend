from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import User


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
    effectivePermissions = serializers.SerializerMethodField()

    def get_isCompanyOwner(self, obj):
        from .permissions import is_company_owner
        return is_company_owner(obj)

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
            "id", "name", "email", "phone", "avatar",
            "company", "branch", "role",
            "employeeProfileId", "employeeDisplayNo", "effectivePermissions", "isCompanyOwner", "paidAccessRequired",
            "is_staff", "is_superuser", "email_verified",
            "created_at",
        ]


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            email=attrs["email"].lower(),
            password=attrs["password"],
        )
        if not user:
            raise serializers.ValidationError("Invalid email or password.")
        if not user.is_active:
            raise serializers.ValidationError("This account is disabled.")
        # An old password must not be enough to sign in after the workshop,
        # login role or role's company assignment has been deactivated.
        if user.company_id:
            role = user.role
            if (not user.company or not user.company.is_active
                    or not role or not role.is_active
                    or role.company_id != user.company_id):
                raise serializers.ValidationError("This account cannot sign in. Contact your workshop administrator.")
        attrs["user"] = user
        return attrs


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


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
    email = serializers.EmailField()

    def validate_email(self, value):
        value = value.lower().strip()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value
