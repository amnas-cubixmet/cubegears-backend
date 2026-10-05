from rest_framework import serializers
from .models import StockCategory,Supplier,StockItem,StockMovement,PurchaseOrder,StockTransfer,StockAudit

class StockCategorySerializer(serializers.ModelSerializer):
    class Meta: model=StockCategory; exclude=("company","branch")
class SupplierSerializer(serializers.ModelSerializer):
    class Meta: model=Supplier; exclude=("company","branch")
class StockItemSerializer(serializers.ModelSerializer):
    categoryName=serializers.CharField(source="category.name",read_only=True)
    supplierName=serializers.CharField(source="supplier.name",read_only=True)
    available=serializers.DecimalField(source="available_quantity",max_digits=12,decimal_places=2,read_only=True)
    lowStock=serializers.BooleanField(source="low_stock",read_only=True)
    cost=serializers.DecimalField(source="cost_price",max_digits=12,decimal_places=2,required=False)
    price=serializers.DecimalField(source="selling_price",max_digits=12,decimal_places=2,required=False)
    minimum=serializers.DecimalField(source="minimum_stock",max_digits=12,decimal_places=2,required=False)
    location=serializers.CharField(source="rack",required=False,allow_blank=True)
    class Meta: model=StockItem; exclude=("company","branch")
class StockMovementSerializer(serializers.ModelSerializer):
    itemName=serializers.CharField(source="item.name",read_only=True)
    class Meta: model=StockMovement; exclude=("company","branch")
class PurchaseOrderSerializer(serializers.ModelSerializer):
    class Meta: model=PurchaseOrder; exclude=("company","branch")
class StockTransferSerializer(serializers.ModelSerializer):
    class Meta: model=StockTransfer; exclude=("company","branch")
class StockAuditSerializer(serializers.ModelSerializer):
    class Meta: model=StockAudit; exclude=("company","branch")
