import re
from rest_framework import serializers
from .models import StockCategory,StockItem,StockMovement,PurchaseOrder,StockTransfer,StockAudit

class StockCategorySerializer(serializers.ModelSerializer):
    class Meta: model=StockCategory; exclude=("company","branch")

# Supplier validation is now owned by the suppliers app; legacy imports remain stable.
from apps.suppliers.serializers import SupplierSerializer as SupplierSerializer


class StockItemSerializer(serializers.ModelSerializer):
    sku=serializers.CharField(required=False,allow_blank=True,trim_whitespace=True,max_length=80)
    partNumber=serializers.CharField(source="part_number",required=False,allow_blank=True,max_length=100)
    partName=serializers.CharField(source="name",required=False)
    categoryName=serializers.CharField(source="category.name",read_only=True)
    supplierName=serializers.CharField(source="supplier.name",read_only=True)
    available=serializers.DecimalField(source="available_quantity",max_digits=12,decimal_places=2,read_only=True)
    lowStock=serializers.BooleanField(source="low_stock",read_only=True)
    cost=serializers.DecimalField(source="cost_price",max_digits=12,decimal_places=2,required=False)
    costPrice=serializers.DecimalField(source="cost_price",max_digits=12,decimal_places=2,required=False)
    price=serializers.DecimalField(source="selling_price",max_digits=12,decimal_places=2,required=False)
    sellingPrice=serializers.DecimalField(source="selling_price",max_digits=12,decimal_places=2,required=False)
    minimum=serializers.DecimalField(source="minimum_stock",max_digits=12,decimal_places=2,required=False)
    minimumStock=serializers.DecimalField(source="minimum_stock",max_digits=12,decimal_places=2,required=False)
    reorderLevel=serializers.DecimalField(source="reorder_level",max_digits=12,decimal_places=2,required=False)
    compatibleVehicle=serializers.CharField(source="compatible_vehicle",required=False,allow_blank=True)
    location=serializers.CharField(source="rack",required=False,allow_blank=True)
    hsnCode=serializers.CharField(source="hsn_code",required=False,allow_blank=True)
    onHand=serializers.DecimalField(source="on_hand",max_digits=12,decimal_places=2,required=False)

    def validate_sku(self, value):
        value = value.strip()
        if not value:
            if self.instance is not None:
                raise serializers.ValidationError("An existing SKU cannot be blank.")
            return value  # An empty SKU is generated safely during creation.
        request = self.context.get("request")
        company_id = getattr(getattr(request, "user", None), "company_id", None)
        if company_id:
            qs = StockItem.objects.filter(company_id=company_id, sku__iexact=value)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError("This SKU already exists in your company.")
        return value

    def validate_partNumber(self, value):
        value = value.strip()
        if value and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+#() -]*", value):
            raise serializers.ValidationError("Use a valid manufacturer part reference.")
        return value

    def validate_onHand(self, value):
        if value < 0:
            raise serializers.ValidationError("Stock quantity cannot be negative.")
        return value

    class Meta:
        model=StockItem
        exclude=("company","branch","name","cost_price","selling_price","minimum_stock","reorder_level","compatible_vehicle","hsn_code","on_hand","part_number")

class StockMovementSerializer(serializers.ModelSerializer):
    unitSellingPrice=serializers.DecimalField(source="unit_selling_price",max_digits=12,decimal_places=2,read_only=True)
    itemName=serializers.CharField(source="item.name",read_only=True)
    partName=serializers.CharField(source="item.name",read_only=True)
    sku=serializers.CharField(source="item.sku",read_only=True)
    qty=serializers.DecimalField(source="quantity",max_digits=12,decimal_places=2,read_only=True)
    type=serializers.CharField(source="movement_type",read_only=True)
    class Meta: model=StockMovement; exclude=("company","branch")

class PurchaseOrderSerializer(serializers.ModelSerializer):
    purchaseNo=serializers.CharField(source="number",required=False)
    class Meta: model=PurchaseOrder; exclude=("company","branch","number")

class StockTransferSerializer(serializers.ModelSerializer):
    class Meta: model=StockTransfer; exclude=("company","branch")

class StockAuditSerializer(serializers.ModelSerializer):
    class Meta: model=StockAudit; exclude=("company","branch")
