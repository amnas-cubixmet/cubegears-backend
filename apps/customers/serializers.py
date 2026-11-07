from rest_framework import serializers
from .models import Customer, CustomerActivity, CustomerReminder
from apps.branches.models import Branch

class CustomerSerializer(serializers.ModelSerializer):
    branchName = serializers.CharField(source="branch.name", read_only=True)
    branchId = serializers.UUIDField(source="branch_id", read_only=True)
    branch = serializers.PrimaryKeyRelatedField(
        queryset=Branch.objects.all(), required=False, allow_null=True,
    )
    companyName = serializers.CharField(source="company_name", required=False, allow_blank=True)
    customerType = serializers.CharField(source="customer_type", required=False)
    class Meta:
        model = Customer
        exclude = ("company",)
        read_only_fields = ("id","created_at","updated_at")

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Customer name is required.")
        return value

    def validate_phone(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Customer contact number is required.")
        return value

    def validate_branch(self, branch):
        request = self.context.get("request")
        if branch and (
            branch.company_id != request.user.company_id or not branch.is_active
        ):
            raise serializers.ValidationError("Select an active branch in your company.")
        return branch

class CustomerActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerActivity
        exclude = ("company","branch")

class CustomerReminderSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerReminder
        exclude = ("company","branch")
