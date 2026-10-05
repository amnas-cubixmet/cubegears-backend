from decimal import Decimal
from django.db import models
from common.models import CompanyOwnedModel

class StockCategory(CompanyOwnedModel):
    name=models.CharField(max_length=120)
    description=models.TextField(blank=True)
    is_active=models.BooleanField(default=True)
    class Meta: ordering=["name"]
    def __str__(self): return self.name

class Supplier(CompanyOwnedModel):
    name=models.CharField(max_length=180)
    contact_person=models.CharField(max_length=120,blank=True)
    phone=models.CharField(max_length=30,blank=True)
    email=models.EmailField(blank=True)
    gstin=models.CharField(max_length=20,blank=True)
    address=models.TextField(blank=True)
    is_active=models.BooleanField(default=True)
    def __str__(self): return self.name

class StockItem(CompanyOwnedModel):
    name=models.CharField(max_length=180)
    sku=models.CharField(max_length=80)
    barcode=models.CharField(max_length=100,blank=True)
    category=models.ForeignKey(StockCategory,on_delete=models.SET_NULL,null=True,blank=True,related_name="items")
    brand=models.CharField(max_length=100,blank=True)
    compatible_vehicle=models.CharField(max_length=180,blank=True,default="Universal")
    unit=models.CharField(max_length=30,default="Piece")
    cost_price=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    selling_price=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    on_hand=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    reserved=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    minimum_stock=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    reorder_level=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    rack=models.CharField(max_length=80,blank=True)
    supplier=models.ForeignKey(Supplier,on_delete=models.SET_NULL,null=True,blank=True,related_name="items")
    tax=models.DecimalField(max_digits=5,decimal_places=2,default=Decimal("0"))
    hsn_code=models.CharField(max_length=20,blank=True)
    status=models.CharField(max_length=20,default="Active")
    class Meta:
        ordering=["name"]
        constraints=[models.UniqueConstraint(fields=["company","sku"],name="unique_stock_sku_per_company")]
    @property
    def available_quantity(self): return self.on_hand-self.reserved
    @property
    def low_stock(self): return self.available_quantity <= self.minimum_stock
    def __str__(self): return f"{self.sku} - {self.name}"

class StockMovement(CompanyOwnedModel):
    TYPES=[(x,x) for x in ["purchase","job_issue","sale","return","transfer_in","transfer_out","adjustment","opening"]]
    item=models.ForeignKey(StockItem,on_delete=models.PROTECT,related_name="movements")
    movement_type=models.CharField(max_length=30,choices=TYPES)
    quantity=models.DecimalField(max_digits=12,decimal_places=2)
    unit_cost=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    reference=models.CharField(max_length=100,blank=True)
    job=models.ForeignKey("jobs.Job",on_delete=models.SET_NULL,null=True,blank=True,related_name="stock_movements")
    note=models.TextField(blank=True)
    created_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True)
    class Meta: ordering=["-created_at"]

class PurchaseOrder(CompanyOwnedModel):
    number=models.CharField(max_length=50)
    supplier=models.ForeignKey(Supplier,on_delete=models.PROTECT,related_name="purchase_orders")
    status=models.CharField(max_length=30,default="Draft")
    expected_date=models.DateField(null=True,blank=True)
    items=models.JSONField(default=list,blank=True)
    subtotal=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    tax=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    total=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    class Meta:
        ordering=["-created_at"]
        constraints=[models.UniqueConstraint(fields=["company","number"],name="unique_po_number_per_company")]

class StockTransfer(CompanyOwnedModel):
    number=models.CharField(max_length=50)
    from_branch=models.ForeignKey("branches.Branch",on_delete=models.PROTECT,related_name="stock_transfers_out")
    to_branch=models.ForeignKey("branches.Branch",on_delete=models.PROTECT,related_name="stock_transfers_in")
    status=models.CharField(max_length=30,default="Draft")
    items=models.JSONField(default=list,blank=True)
    transferred_at=models.DateTimeField(null=True,blank=True)

class StockAudit(CompanyOwnedModel):
    item=models.ForeignKey(StockItem,on_delete=models.PROTECT,related_name="audits")
    expected_qty=models.DecimalField(max_digits=12,decimal_places=2)
    counted_qty=models.DecimalField(max_digits=12,decimal_places=2)
    difference=models.DecimalField(max_digits=12,decimal_places=2)
    reason=models.CharField(max_length=255,blank=True)
    created_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True)
