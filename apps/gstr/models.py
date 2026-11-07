"""Audited GST preparation records, separate from official GST portal state."""
from decimal import Decimal
from django.db import models
from common.models import CompanyOwnedModel


class GSTR2BImport(CompanyOwnedModel):
    period = models.CharField(max_length=7)
    fingerprint = models.CharField(max_length=64)
    rows = models.JSONField(default=list)
    unsupported_sections = models.JSONField(default=list)
    imported_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="gst_2b_imports",
    )
    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "period", "fingerprint"],
                name="unique_company_gst_2b_fingerprint",
            ),
        ]


class GSTInputTaxClaim(CompanyOwnedModel):
    """One independently reviewed claim per supplier bill; no automatic tax credit."""
    bill = models.OneToOneField(
        "inventory.SupplierBill", on_delete=models.PROTECT,
        related_name="gst_itc_claim",
    )
    import_batch = models.ForeignKey(
        GSTR2BImport, on_delete=models.PROTECT, related_name="approved_claims",
    )
    period = models.CharField(max_length=7)
    cgst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    sgst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    igst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    eligibility_reason = models.CharField(max_length=500)
    approved_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
    )
    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "bill"], name="unique_gst_itc_bill_claim",
            ),
        ]


class GSTInputTaxReversal(CompanyOwnedModel):
    """Automatic credit reduction when previously claimed goods are returned."""
    claim = models.ForeignKey(
        GSTInputTaxClaim, on_delete=models.PROTECT, related_name="reversals",
    )
    debit_note = models.OneToOneField(
        "inventory.SupplierDebitNote", on_delete=models.PROTECT,
        related_name="gst_itc_reversal",
    )
    period = models.CharField(max_length=7)
    cgst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    sgst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    igst = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    class Meta:
        ordering = ["-created_at"]


class GSTFilingReview(CompanyOwnedModel):
    """Internal review only; GST portal acknowledgment is not independently verified."""
    period = models.CharField(max_length=7)
    status = models.CharField(max_length=24, default="Reviewed")
    reference = models.CharField(max_length=120, blank=True)
    filed_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=1000)
    reviewed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
    )
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "period"], name="unique_company_gst_filing_review",
            ),
        ]
