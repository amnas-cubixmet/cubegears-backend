from rest_framework import serializers
from .models import Role
from apps.accounts.permissions import can_delegate


class RoleSerializer(serializers.ModelSerializer):
    usersCount = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = "__all__"
        read_only_fields = ("id", "company", "created_at", "usersCount", "is_system")

    def get_usersCount(self, obj):
        return obj.users.filter(is_active=True).count()

    def validate_permissions(self, value):
        user = self.context['request'].user
        if not isinstance(value, list) or any(not isinstance(code, str) for code in value):
            raise serializers.ValidationError("Permissions must be a list of strings.")
        if not user.is_superuser and (any(code.startswith('panel.') for code in value) or not can_delegate(user, value)):
            raise serializers.ValidationError("You cannot grant permissions beyond your own role.")
        return list(dict.fromkeys(value))
