from rest_framework import serializers
from .models import Vehicle

class VehicleSerializer(serializers.ModelSerializer):
    customerId=serializers.UUIDField(source="customer_id",read_only=True)
    customerName=serializers.CharField(source="customer.name",read_only=True)
    regNo=serializers.CharField(source="registration",required=False)
    licensePlate=serializers.CharField(source="registration",read_only=True)
    fuelType=serializers.CharField(source="fuel_type",required=False,allow_blank=True)
    engineNo=serializers.CharField(source="engine_no",required=False,allow_blank=True)
    kilometres=serializers.IntegerField(source="odometer",required=False)
    lastServiceDate=serializers.DateField(source="last_service_date",required=False,allow_null=True)
    nextServiceDue=serializers.DateField(source="next_service_due",required=False,allow_null=True)
    insuranceExpiry=serializers.DateField(source="insurance_expiry",required=False,allow_null=True)
    customer=serializers.PrimaryKeyRelatedField(queryset=__import__("apps.customers.models",fromlist=["Customer"]).Customer.objects.all(),write_only=True,required=False)
    class Meta:
        model=Vehicle
        exclude=("company","branch")
        read_only_fields=("id","created_at","updated_at")
