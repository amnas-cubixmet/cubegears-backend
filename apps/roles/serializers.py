from rest_framework import serializers
from .models import Role
from apps.accounts.permissions import can_delegate
from .assignment import STAFF_PERMISSION_CODES


class RoleSerializer(serializers.ModelSerializer):
    usersCount = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = "__all__"
        read_only_fields = ("id", "company", "created_at", "usersCount", "is_system")

    def get_usersCount(self, obj):
        return obj.users.filter(is_active=True).count()

    def validate_name(self, value):
        value = value.strip()
        roles = Role.objects.filter(company=self.context['request'].user.company, name__iexact=value)
        if self.instance:
            roles = roles.exclude(pk=self.instance.pk)
        if not value or roles.exists():
            raise serializers.ValidationError('Choose a unique role name in this company.')
        return value

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
        if any(code not in STAFF_PERMISSION_CODES for code in value):
            raise serializers.ValidationError('Choose known staff permissions available to this company.')
        previous = set(self.instance.permissions or []) if self.instance else set()
        additions = [code for code in value if code not in previous]
        if not can_delegate(self.context["request"].user, additions):
            raise serializers.ValidationError("You cannot grant permissions beyond your own role.")
        return list(dict.fromkeys(value))
