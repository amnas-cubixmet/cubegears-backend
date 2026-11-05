import uuid
import django.db.models.deletion
from django.db import migrations, models
import apps.customers.models


class Migration(migrations.Migration):
    dependencies = [("customers", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="CustomerPortalAccess",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("nonce", models.CharField(default=apps.customers.models.portal_access_nonce, max_length=64)),
                ("token_hash", models.CharField(blank=True, max_length=64)),
                ("is_active", models.BooleanField(default=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("rotated_at", models.DateTimeField(blank=True, null=True)),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="branches.branch")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("customer", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="portal_access", to="customers.customer")),
            ],
        ),
    ]
