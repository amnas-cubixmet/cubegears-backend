from rest_framework import serializers
from .models import Role


class RoleSerializer(serializers.ModelSerializer):
    usersCount = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = "__all__"
        read_only_fields = ("id", "company", "created_at", "usersCount")

    def get_usersCount(self, obj):
        return obj.users.filter(is_active=True).count()
