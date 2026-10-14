import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("saas", "0004_checkout_price")]
    operations = [
        migrations.CreateModel(
            name="SubscriptionExpiryReminder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("days_before", models.PositiveSmallIntegerField()),
                ("email", models.EmailField(max_length=254)),
                ("sent_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("subscription", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="expiry_reminders", to="saas.subscription")),
            ],
        ),
        migrations.AddConstraint(
            model_name="subscriptionexpiryreminder",
            constraint=models.UniqueConstraint(fields=("subscription", "days_before"), name="saas_unique_expiry_reminder"),
        ),
    ]
