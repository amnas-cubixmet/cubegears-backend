from rest_framework import serializers
from .models import Customer, CustomerActivity, CustomerReminder

class CustomerSerializer(serializers.ModelSerializer):
    branchName = serializers.CharField(source="branch.name", read_only=True)
    companyName = serializers.CharField(source="company_name", required=False, allow_blank=True)
    customerType = serializers.CharField(source="customer_type", required=False)
    class Meta:
        model = Customer
        exclude = ("company","branch")
        read_only_fields = ("id","created_at","updated_at")

class CustomerActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerActivity
        exclude = ("company","branch")

class CustomerReminderSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerReminder
        exclude = ("company","branch")
