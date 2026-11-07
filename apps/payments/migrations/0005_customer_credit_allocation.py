"""Part 11: persist customer advances applied to invoices or refunded."""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0004_payment_idempotency_key"),
        ("invoices", "0005_invoiceitem_tax_choice"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomerCreditAllocation",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("amount", models.DecimalField(max_digits=12, decimal_places=2)),
                ("idempotency_key", models.CharField(max_length=128, null=True, blank=True, default=None)),
                ("reversed_at", models.DateTimeField(null=True, blank=True)),
                ("reversal_reason", models.TextField(blank=True)),
                ("advance_payment", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT, related_name="credit_allocations",
                    to="payments.payment",
                )),
                ("branch", models.ForeignKey(
                    on_delete=django.db.models.deletion.SET_NULL,
                    null=True, blank=True, to="branches.branch",
                )),
                ("company", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, to="companies.company",
                )),
                ("created_by", models.ForeignKey(
                    on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True,
                    related_name="customer_credit_allocations", to=settings.AUTH_USER_MODEL,
                )),
                ("invoice", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT, null=True, blank=True,
                    related_name="advance_allocations", to="invoices.invoice",
                )),
                ("refund_payment", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT, null=True, blank=True,
                    related_name="refunded_advance_allocation", to="payments.payment",
                )),
                ("reversed_by", models.ForeignKey(
                    on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True,
                    related_name="reversed_customer_credit_allocations", to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="customercreditallocation",
            constraint=models.UniqueConstraint(
                fields=("company", "idempotency_key"),
                name="unique_customer_credit_allocation_request",
            ),
        ),
        migrations.AddConstraint(
            model_name="customercreditallocation",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(invoice__isnull=False, refund_payment__isnull=True)
                    | models.Q(invoice__isnull=True, refund_payment__isnull=False)
                ),
                name="customer_credit_one_application_destination",
            ),
        ),
        migrations.AddConstraint(
            model_name="customercreditallocation",
            constraint=models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="customer_credit_allocation_positive",
            ),
        ),
    ]
