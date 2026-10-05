from rest_framework import serializers
from .models import StockCategory,Supplier,StockItem,StockMovement,PurchaseOrder,StockTransfer,StockAudit

class StockCategorySerializer(serializers.ModelSerializer):
    class Meta: model=StockCategory; exclude=("company","branch")

class SupplierSerializer(serializers.ModelSerializer):
    gstNo=serializers.CharField(source="gstin",required=False,allow_blank=True)
    class Meta: model=Supplier; exclude=("company","branch","gstin")

class StockItemSerializer(serializers.ModelSerializer):
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
    class Meta:
        model=StockItem
        exclude=("company","branch","name","cost_price","selling_price","minimum_stock","reorder_level","compatible_vehicle","hsn_code","on_hand")

class StockMovementSerializer(serializers.ModelSerializer):
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
