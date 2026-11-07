"""Additive tax review schema; never recalculates or modifies historic invoices."""
import uuid
from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


def base_fields():
    return [
        ("id", models.UUIDField(primary_key=True, serialize=False, editable=False, default=uuid.uuid4)),
        ("created_at", models.DateTimeField(auto_now_add=True)),
        ("updated_at", models.DateTimeField(auto_now=True)),
        ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
        ("branch", models.ForeignKey(to="branches.branch", null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL)),
    ]


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("inventory", "0012_supplier_debit_notes_refunds"),
        ("accounts", "0002_mobile_login_staff_tokens"),
        ("companies", "0008_require_customer_estimate_approval"),
    ]
    operations = [
        migrations.CreateModel(
            name="GSTR2BImport",
            fields=base_fields() + [
                ("period", models.CharField(max_length=7)),
                ("fingerprint", models.CharField(max_length=64)),
                ("rows", models.JSONField(default=list)),
                ("unsupported_sections", models.JSONField(default=list)),
                ("imported_by", models.ForeignKey(
                    to="accounts.user", on_delete=django.db.models.deletion.SET_NULL,
                    null=True, blank=True, related_name="gst_2b_imports",
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="GSTInputTaxClaim",
            fields=base_fields() + [
                ("period", models.CharField(max_length=7)),
                ("cgst", models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))),
                ("sgst", models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))),
                ("igst", models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))),
                ("eligibility_reason", models.CharField(max_length=500)),
                ("bill", models.OneToOneField(
                    to="inventory.supplierbill", on_delete=django.db.models.deletion.PROTECT,
                    related_name="gst_itc_claim",
                )),
                ("import_batch", models.ForeignKey(
                    to="gstr.gstr2bimport", on_delete=django.db.models.deletion.PROTECT,
                    related_name="approved_claims",
                )),
                ("approved_by", models.ForeignKey(
                    to="accounts.user", on_delete=django.db.models.deletion.SET_NULL,
                    null=True, blank=True,
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="GSTInputTaxReversal",
            fields=base_fields() + [
                ("period", models.CharField(max_length=7)),
                ("cgst", models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))),
                ("sgst", models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))),
                ("igst", models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))),
                ("claim", models.ForeignKey(
                    to="gstr.gstinputtaxclaim", on_delete=django.db.models.deletion.PROTECT,
                    related_name="reversals",
                )),
                ("debit_note", models.OneToOneField(
                    to="inventory.supplierdebitnote", on_delete=django.db.models.deletion.PROTECT,
                    related_name="gst_itc_reversal",
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="GSTFilingReview",
            fields=base_fields() + [
                ("period", models.CharField(max_length=7)),
                ("status", models.CharField(max_length=24, default="Reviewed")),
                ("reference", models.CharField(max_length=120, blank=True)),
                ("filed_on", models.DateField(null=True, blank=True)),
                ("note", models.CharField(max_length=1000)),
                ("reviewed_by", models.ForeignKey(
                    to="accounts.user", on_delete=django.db.models.deletion.SET_NULL,
                    null=True, blank=True,
                )),
            ],
        ),
        migrations.AddConstraint(
            model_name="gstr2bimport",
            constraint=models.UniqueConstraint(
                fields=["company", "period", "fingerprint"],
                name="unique_company_gst_2b_fingerprint",
            ),
        ),
        migrations.AddConstraint(
            model_name="gstinputtaxclaim",
            constraint=models.UniqueConstraint(
                fields=["company", "bill"], name="unique_gst_itc_bill_claim",
            ),
        ),
        migrations.AddConstraint(
            model_name="gstfilingreview",
            constraint=models.UniqueConstraint(
                fields=["company", "period"], name="unique_company_gst_filing_review",
            ),
        ),
    ]
