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

    class Meta:
        model = User
        fields = [
            "id", "name", "email", "phone", "avatar",
            "company", "branch", "role",
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
        attrs["user"] = user
        return attrs

class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

class ResetPasswordSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        validate_password(value)
        return value

class MagicLinkRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

class MagicLinkVerifySerializer(serializers.Serializer):
    token = serializers.CharField()

class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        validate_password(value)
        return value
