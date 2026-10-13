from rest_framework import serializers
from .models import Role


class RoleSerializer(serializers.ModelSerializer):
    usersCount = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ("id", "company", "name", "code", "permissions", "is_system", "is_active", "created_at", "usersCount")
        read_only_fields = ("id", "company", "is_system", "created_at", "usersCount")

    def validate_code(self, value):
        code = value.strip().upper()
        if code in {"SUPER_ADMIN", "PLATFORM_ADMIN", "ADMIN"}:
            raise serializers.ValidationError("This role code is reserved.")
        return code

    def validate_permissions(self, value):
        # Only the bootstrap/signup flow can issue a tenant wildcard.
        if "*" in value:
            raise serializers.ValidationError("Full-access permissions are reserved for the protected company owner role.")
        cleaned = list(dict.fromkeys(value))
        if any(not isinstance(code, str) or not code.strip() for code in cleaned):
            raise serializers.ValidationError("Permissions must be non-empty codes.")
        return cleaned

    def get_usersCount(self, obj):
        return obj.users.filter(is_active=True).count()
