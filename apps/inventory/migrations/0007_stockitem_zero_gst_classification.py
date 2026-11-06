"""Preserve the type of a 0% stock item independently of its GST rate."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0006_supplier_line_payment_allocation"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockitem",
            name="gst_zero_classification",
            field=models.CharField(
                blank=True,
                choices=[
                    ("exempt", "Exempt"),
                    ("nil_rated", "Nil-rated"),
                    ("zero_rated", "Zero-rated"),
                    ("non_gst", "Non-GST"),
                ],
                default="",
                max_length=16,
            ),
        ),
    ]
