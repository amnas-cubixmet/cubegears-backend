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
    # Manufacturer reference; never inferred from a workshop SKU.
    part_number=models.CharField(max_length=100,blank=True,default="")
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
    ZERO_GST_CLASSIFICATION_CHOICES = [
        ("exempt", "Exempt"),
        ("nil_rated", "Nil-rated"),
        ("zero_rated", "Zero-rated"),
        ("non_gst", "Non-GST"),
    ]
    # 0% item classification is distinct from the numeric tax rate.
    # Blank remains valid for existing imported/historical stock items.
    gst_zero_classification=models.CharField(
        max_length=16, choices=ZERO_GST_CLASSIFICATION_CHOICES, blank=True, default=""
    )
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

class StockBranchBalance(CompanyOwnedModel):
    """Quantity available at one workshop branch; StockItem.on_hand is the total."""
    item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="branch_balances")
    branch = models.ForeignKey(
        "branches.Branch", on_delete=models.PROTECT, related_name="inventory_balances",
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    reserved = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "item", "branch"], name="unique_inventory_branch_balance",
            ),
            models.CheckConstraint(condition=models.Q(quantity__gte=0), name="inventory_branch_nonnegative"),
            models.CheckConstraint(condition=models.Q(reserved__gte=0), name="inventory_branch_reserved_nonnegative"),
        ]


class StockMovement(CompanyOwnedModel):
    TYPES=[(x,x) for x in ["purchase","job_issue","sale","return","transfer_in","transfer_out","adjustment","opening"]]
    item=models.ForeignKey(StockItem,on_delete=models.PROTECT,related_name="movements")
    request_key=models.CharField(max_length=120,null=True,blank=True,default=None)
    origin_movement=models.ForeignKey("self",on_delete=models.PROTECT,null=True,blank=True,related_name="return_movements")
    movement_type=models.CharField(max_length=30,choices=TYPES)
    quantity=models.DecimalField(max_digits=12,decimal_places=2)
    unit_cost=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    unit_selling_price=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    reference=models.CharField(max_length=100,blank=True)
    job=models.ForeignKey("jobs.Job",on_delete=models.SET_NULL,null=True,blank=True,related_name="stock_movements")
    note=models.TextField(blank=True)
    created_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True)
    class Meta:
        ordering=["-created_at"]
        constraints=[
            models.UniqueConstraint(fields=["company","request_key"],name="unique_inventory_movement_request"),
        ]

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
    request_key=models.CharField(max_length=120,null=True,blank=True,default=None)
    number=models.CharField(max_length=50)
    from_branch=models.ForeignKey("branches.Branch",on_delete=models.PROTECT,related_name="stock_transfers_out")
    to_branch=models.ForeignKey("branches.Branch",on_delete=models.PROTECT,related_name="stock_transfers_in")
    status=models.CharField(max_length=30,default="Draft")
    items=models.JSONField(default=list,blank=True)
    transferred_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints=[
            models.UniqueConstraint(fields=["company","request_key"],name="unique_inventory_transfer_request"),
        ]

class StockAudit(CompanyOwnedModel):
    request_key=models.CharField(max_length=120,null=True,blank=True,default=None)
    item=models.ForeignKey(StockItem,on_delete=models.PROTECT,related_name="audits")
    expected_qty=models.DecimalField(max_digits=12,decimal_places=2)
    counted_qty=models.DecimalField(max_digits=12,decimal_places=2)
    difference=models.DecimalField(max_digits=12,decimal_places=2)
    reason=models.CharField(max_length=255,blank=True)
    created_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True)
    class Meta:
        constraints=[
            models.UniqueConstraint(fields=["company","request_key"],name="unique_inventory_count_request"),
        ]


# Supplier financial records are separate from PurchaseOrder drafts and from
# customer Payment. A posted bill is a payable, not a second expense.
class WorkshopFinancialAccount(CompanyOwnedModel):
    KIND_CHOICES = [("Cash", "Cash"), ("Bank", "Bank"), ("UPI", "UPI"), ("Wallet", "Wallet"), ("Other", "Other")]
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    # Bank/account detail fields
    bank_name = models.CharField(max_length=120, blank=True, default="")
    account_number = models.CharField(max_length=50, blank=True, default="")
    ifsc = models.CharField(max_length=20, blank=True, default="")
    branch_name = models.CharField(max_length=120, blank=True, default="")
    opening_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True, default="")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_workshop_money_account")]


class SupplierBill(CompanyOwnedModel):
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="accounting_bills")
    invoice_number = models.CharField(max_length=100)
    invoice_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    related_job = models.ForeignKey("jobs.Job", on_delete=models.PROTECT, null=True, blank=True)
    # Validated, server-priced snapshot. No stock movement before posting.
    items = models.JSONField(default=list)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    tax = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    status = models.CharField(max_length=12, default="Draft", choices=[("Draft", "Draft"), ("Posted", "Posted")])
    receive_into_stock = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="supplier_bills_created")
    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "supplier", "invoice_number"],
                name="unique_supplier_bill_reference_per_company",
            ),
        ]


class SupplierPayment(CompanyOwnedModel):
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="accounting_payments")
    account = models.ForeignKey(WorkshopFinancialAccount, on_delete=models.PROTECT, related_name="supplier_payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    kind = models.CharField(max_length=12, choices=[("Payment", "Payment"), ("Advance", "Advance")])
    method = models.CharField(max_length=30)
    reference = models.CharField(max_length=120, blank=True)
    paid_on = models.DateField()
    idempotency_key = models.CharField(max_length=120)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="supplier_payments_recorded")
    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "idempotency_key"], name="unique_supplier_payment_request"),
        ]


class SupplierPaymentAllocation(CompanyOwnedModel):
    payment = models.ForeignKey(SupplierPayment, on_delete=models.PROTECT, related_name="allocations")
    bill = models.ForeignKey(SupplierBill, on_delete=models.PROTECT, related_name="payment_allocations")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["payment", "bill"], name="unique_payment_allocation_per_bill"),
        ]


class SupplierLinePaymentAllocation(CompanyOwnedModel):
    """Optional item-wise detail of one bill allocation (not another liability)."""
    allocation = models.ForeignKey(
        SupplierPaymentAllocation, on_delete=models.PROTECT, related_name="line_allocations",
    )
    line_index = models.PositiveIntegerField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["allocation", "line_index"], name="unique_supplier_bill_line_application",
            ),
        ]


class WorkshopAccountEntry(CompanyOwnedModel):
    """Immutable signed cash/bank ledger movement. Negative = money out."""
    account = models.ForeignKey(WorkshopFinancialAccount, on_delete=models.PROTECT, related_name="entries")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    entry_type = models.CharField(max_length=30)
    source_key = models.CharField(max_length=160)
    reference = models.CharField(max_length=120, blank=True)
    posted_at = models.DateTimeField()
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    class Meta:
        ordering = ["posted_at", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "source_key"], name="unique_workshop_account_posting"),
        ]


class FinanceTransfer(CompanyOwnedModel):
    """Atomic inter-account transfer. Never an income or expense."""
    from_account = models.ForeignKey(WorkshopFinancialAccount, on_delete=models.PROTECT, related_name="transfers_out")
    to_account = models.ForeignKey(WorkshopFinancialAccount, on_delete=models.PROTECT, related_name="transfers_in")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    transfer_date = models.DateField()
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=160, unique=False)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    class Meta:
        ordering = ["-transfer_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "idempotency_key"], name="unique_finance_transfer")
        ]
