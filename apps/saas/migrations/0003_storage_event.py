import uuid
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("saas", "0002_dynamic_plans"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(
        name="StorageEvent",
        fields=[
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("event_type", models.CharField(max_length=12, choices=[("UPLOAD","Uploaded"),("DELETE","Deleted"),("BASELINE","Existing file baseline")], db_index=True)),
            ("file_id_snapshot", models.UUIDField(db_index=True)),
            ("name_snapshot", models.CharField(max_length=255)),
            ("category_snapshot", models.CharField(max_length=80, default="General")),
            ("mime_type_snapshot", models.CharField(max_length=120, blank=True)),
            ("file_size_bytes", models.BigIntegerField(default=0)),
            ("delta_bytes", models.BigIntegerField(default=0)),
            ("storage_before_bytes", models.BigIntegerField(default=0)),
            ("storage_after_bytes", models.BigIntegerField(default=0)),
            ("file_count_after", models.PositiveIntegerField(default=0)),
            ("occurred_at", models.DateTimeField(default=django.utils.timezone.now, db_index=True)),
            ("actor", models.ForeignKey(to=settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, related_name="storage_events")),
            ("branch", models.ForeignKey(to="branches.branch", null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL)),
            ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
        ],
        options={"ordering":["-occurred_at","-created_at","-id"],"indexes":[models.Index(fields=["company","occurred_at"],name="saas_se_company_at"),models.Index(fields=["company","event_type"],name="saas_se_company_kind")]},
    )]
