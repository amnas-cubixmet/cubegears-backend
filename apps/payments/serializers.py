from rest_framework import serializers
from .models import Payment
class PaymentSerializer(serializers.ModelSerializer):
    customerName=serializers.CharField(source="customer.name",read_only=True)
    invoiceNo=serializers.CharField(source="invoice.number",read_only=True)
    recordedBy=serializers.CharField(source="recorded_by.name",read_only=True)
    class Meta: model=Payment; exclude=("company","branch")
