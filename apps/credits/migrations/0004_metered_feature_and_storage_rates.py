from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("credits", "0003_hosted_payment_links")]

    operations = [
        migrations.AddField(
            model_name="featurecreditprice", name="activation_credits",
            field=models.DecimalField(decimal_places=2, max_digits=12, default=Decimal("0.00")),
        ),
        migrations.AddField(
            model_name="featurecreditprice", name="per_user_credits",
            field=models.DecimalField(decimal_places=2, max_digits=12, default=Decimal("0.00")),
        ),
        migrations.AddField(
            model_name="featurecreditprice", name="billing_period",
            field=models.CharField(
                max_length=8, default="monthly",
                choices=[("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "30 days")],
            ),
        ),
        migrations.CreateModel(
            name="StorageCreditPolicy",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name="ID")),
                ("billing_period", models.CharField(
                    choices=[("daily", "Daily"), ("monthly", "Monthly")],
                    default="monthly", max_length=8,
                )),
                ("included_gb", models.DecimalField(
                    max_digits=12, decimal_places=3, default=Decimal("0"),
                )),
                ("credits_per_gb", models.DecimalField(
                    max_digits=12, decimal_places=2, default=Decimal("0"),
                )),
                ("is_active", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
