from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0003_payment_account_payment_category_payment_direction_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="idempotency_key",
            field=models.CharField(blank=True, default=None, max_length=128, null=True),
        ),
        migrations.AddConstraint(
            model_name="payment",
            constraint=models.UniqueConstraint(
                fields=("company", "idempotency_key"),
                name="unique_customer_payment_request",
            ),
        ),
    ]
