from rest_framework import serializers
from .models import Customer, CustomerActivity, CustomerReminder
from apps.branches.models import Branch
from common.phone_numbers import normalize_contact_phone

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
        return normalize_contact_phone(value, required=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # One phone contact: the same number is used for manual WhatsApp
        # sharing. Old records remain untouched until the phone is updated.
        if "phone" in attrs:
            attrs["whatsapp"] = attrs["phone"]
        elif "whatsapp" in attrs:
            # Separate WhatsApp numbers are no longer accepted; keep the
            # existing saved phone when this PATCH does not update phone.
            attrs["whatsapp"] = getattr(self.instance, "phone", "")
        return attrs

    def validate_branch(self, branch):
        request = self.context.get("request")
        from apps.accounts.branch_access import authorized_branch_ids
        if branch and (
            branch.company_id != request.user.company_id or not branch.is_active
            or branch.pk not in authorized_branch_ids(request.user)
        ):
            raise serializers.ValidationError("You may assign customers only to an authorized active branch.")
        return branch

class CustomerActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerActivity
        exclude = ("company","branch")

class CustomerReminderSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerReminder
        exclude = ("company","branch")
