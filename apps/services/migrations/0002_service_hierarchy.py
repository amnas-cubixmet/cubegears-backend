from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("services", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="servicecategory",
            name="parent",
            field=models.ForeignKey(
                to="services.servicecategory", null=True, blank=True,
                on_delete=django.db.models.deletion.PROTECT, related_name="children",
            ),
        ),
        migrations.AddField(
            model_name="service",
            name="unit",
            field=models.CharField(max_length=30, default="Job"),
        ),
        migrations.AddField(
            model_name="service",
            name="pricing_type",
            field=models.CharField(max_length=30, default="Fixed"),
        ),
        migrations.AddField(
            model_name="service",
            name="checklist",
            field=models.JSONField(default=list, blank=True),
        ),
        migrations.AddField(
            model_name="service",
            name="tax_treatment",
            field=models.CharField(
                max_length=20, default="taxable",
                choices=[(x, x) for x in ("taxable","exempt","nil_rated","non_gst","out_of_scope")],
            ),
        ),
    ]
