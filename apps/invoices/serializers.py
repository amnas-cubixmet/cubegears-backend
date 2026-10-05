from decimal import Decimal
from rest_framework import serializers
from .models import Invoice,InvoiceItem,EWayBill

class InvoiceItemSerializer(serializers.ModelSerializer):
    type=serializers.CharField(source="item_type",required=False)
    hsnCode=serializers.CharField(source="hsn_sac",required=False,allow_blank=True)
    qty=serializers.DecimalField(source="quantity",max_digits=12,decimal_places=2,required=False)
    purchasePrice=serializers.DecimalField(source="purchase_price",max_digits=12,decimal_places=2,required=False)
    taxRate=serializers.DecimalField(source="tax_rate",max_digits=5,decimal_places=2,required=False)
    inventoryId=serializers.UUIDField(source="stock_item_id",required=False,allow_null=True)
    class Meta: model=InvoiceItem; exclude=("invoice",)

class InvoiceSerializer(serializers.ModelSerializer):
    items=InvoiceItemSerializer(many=True,required=False)
    customerName=serializers.CharField(source="customer.name",read_only=True)
    jobCardNo=serializers.CharField(source="job.job_number",read_only=True)
    paymentMode=serializers.CharField(source="payment_mode",required=False,allow_blank=True)
    paymentTerms=serializers.CharField(source="payment_terms",required=False,allow_blank=True)
    class Meta: model=Invoice; exclude=("company","branch")
    def create(self,validated_data):
        items=validated_data.pop("items",[])
        obj=Invoice.objects.create(**validated_data)
        for row in items: InvoiceItem.objects.create(invoice=obj,**row)
        return obj
    def update(self,instance,validated_data):
        items=validated_data.pop("items",None)
        for k,v in validated_data.items(): setattr(instance,k,v)
        instance.save()
        if items is not None:
            instance.items.all().delete()
            for row in items: InvoiceItem.objects.create(invoice=instance,**row)
        return instance
class EWayBillSerializer(serializers.ModelSerializer):
    class Meta: model=EWayBill; exclude=("company","branch")
