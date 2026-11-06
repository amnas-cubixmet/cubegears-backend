"""Keep posted payment history immutable and attach reversible audit fields."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name="payment", name="reversed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="payment", name="reversal_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="payment", name="reversed_by",
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL, blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reversed_customer_payments",
            ),
        ),
    ]
