from decimal import Decimal
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("saas", "0003_razorpay_plan_payments")]
    operations = [
        migrations.AddField(
            model_name="subscriptionplan",
            name="checkout_price",
            field=models.DecimalField(
                max_digits=12, decimal_places=2, default=Decimal("0"),
                help_text="One-time Razorpay payment for the configured access duration.",
            ),
        ),
        migrations.AlterField(
            model_name="razorpayplanpayment",
            name="cycle",
            field=models.CharField(max_length=10, choices=[("term", "One-time access period")]),
        ),
    ]
