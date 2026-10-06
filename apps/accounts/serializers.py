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