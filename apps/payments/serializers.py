from rest_framework import serializers
from .models import Payment
class PaymentSerializer(serializers.ModelSerializer):
    customerName=serializers.CharField(source="customer.name",read_only=True,default="")
    invoiceNo=serializers.CharField(source="invoice.number",read_only=True,default="")
    recordedBy=serializers.CharField(source="recorded_by.name",read_only=True,default="")
    # account is an optional UUID FK — write by ID, read as UUID string
    account=serializers.PrimaryKeyRelatedField(
        queryset=__import__("apps.inventory.models",fromlist=["WorkshopFinancialAccount"]).WorkshopFinancialAccount.objects.all(),
        required=False, allow_null=True,
    )
    class Meta:
        model = Payment
        exclude = ("company", "branch")
        read_only_fields = ("recorded_by", "reversed_at", "reversed_by", "reversal_reason")
