from decimal import Decimal
from django.db import models
from common.models import CompanyOwnedModel

class Payment(CompanyOwnedModel):
    customer=models.ForeignKey("customers.Customer",on_delete=models.PROTECT,related_name="payments",null=True,blank=True)
    invoice=models.ForeignKey("invoices.Invoice",on_delete=models.SET_NULL,null=True,blank=True,related_name="payments")
    date=models.DateField()
    amount=models.DecimalField(max_digits=12,decimal_places=2,default=Decimal("0"))
    method=models.CharField(max_length=50,default="Cash")
    reference=models.CharField(max_length=120,blank=True)
    status=models.CharField(max_length=30,default="Completed")
    notes=models.TextField(blank=True)
    recorded_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True)
    reversed_at=models.DateTimeField(null=True,blank=True)
    reversed_by=models.ForeignKey("accounts.User",on_delete=models.SET_NULL,null=True,blank=True,related_name="reversed_customer_payments")
    reversal_reason=models.TextField(blank=True)
    # Finance account linkage
    account=models.ForeignKey("inventory.WorkshopFinancialAccount",on_delete=models.SET_NULL,null=True,blank=True,related_name="customer_payments")
    direction=models.CharField(max_length=10,default="IN",choices=[("IN","IN"),("OUT","OUT")])
    category=models.CharField(max_length=80,blank=True,default="")
    idempotency_key=models.CharField(max_length=128,null=True,blank=True,default=None)
    class Meta:
        ordering=["-date","-created_at"]
        constraints=[models.UniqueConstraint(fields=["company","idempotency_key"],name="unique_customer_payment_request")]


class CustomerCreditAllocation(CompanyOwnedModel):
    """Append-only customer advance usage, either invoice settlement or refund.

    A pure invoice allocation never posts another cash/bank entry. A refund
    references its real outgoing Payment which alone posts the cash movement.
    """
    advance_payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, related_name="credit_allocations",
    )
    invoice = models.ForeignKey(
        "invoices.Invoice", on_delete=models.PROTECT, related_name="advance_allocations",
        null=True, blank=True,
    )
    refund_payment = models.OneToOneField(
        Payment, on_delete=models.PROTECT, related_name="refunded_advance_allocation",
        null=True, blank=True,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    idempotency_key = models.CharField(max_length=128, null=True, blank=True, default=None)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="customer_credit_allocations",
    )
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reversed_customer_credit_allocations",
    )
    reversal_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "idempotency_key"],
                name="unique_customer_credit_allocation_request",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(invoice__isnull=False, refund_payment__isnull=True)
                    | models.Q(invoice__isnull=True, refund_payment__isnull=False)
                ),
                name="customer_credit_one_application_destination",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="customer_credit_allocation_positive",
            ),
        ]
