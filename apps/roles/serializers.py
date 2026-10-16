from rest_framework import serializers
from .models import Role
from apps.roles.permission_catalog import TENANT_PERMISSIONS


class RoleSerializer(serializers.ModelSerializer):
    usersCount = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = "__all__"
        read_only_fields = ("id", "company", "created_at", "usersCount", "is_system")

    def get_usersCount(self, obj):
        return obj.users.filter(is_active=True).count()

    def validate_code(self, value):
        code = str(value or "").upper().strip()
        if code in {"SUPER_ADMIN", "OWNER", "ADMIN_OWNER"} or not code:
            raise serializers.ValidationError("Choose a valid, non-owner role code.")
        return code

    def validate_permissions(self, value):
        if not isinstance(value, list) or any(not isinstance(code, str) for code in value):
            raise serializers.ValidationError("Permissions must be a list of strings.")
        if "*" in value or any(
            code.startswith("panel.") or code.startswith("billing.") or code == "company.manage"
            for code in value
        ):
            raise serializers.ValidationError("Staff roles cannot receive owner or platform authority.")
        # Owner controls role definitions, while feature entitlements control
        # actual API use. An expired or unpurchased feature may still be
        # configured in a role without granting access to that feature.
        invalid = sorted(set(value) - set(TENANT_PERMISSIONS))
        if invalid:
            raise serializers.ValidationError("Unknown or unsupported permissions.")
        return list(dict.fromkeys(value))
