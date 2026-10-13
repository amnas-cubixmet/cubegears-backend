from rest_framework import serializers
from .models import Company

class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = "__all__"
        # Company users cannot change subscription, tenant identity or activation.
        read_only_fields = ("id", "slug", "plan", "is_active", "created_at", "updated_at")
