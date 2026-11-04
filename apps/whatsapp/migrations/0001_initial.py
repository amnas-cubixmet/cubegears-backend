import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("companies", "0004_lock_existing_company_modules"),
        ("customers", "0002_customer_portal_access"),
        ("jobs", "0007_outside_part_bill"),
        ("branches", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="WhatsAppBusinessConnection",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("waba_id", models.CharField(max_length=100, blank=True)),
                ("phone_number_id", models.CharField(max_length=100, blank=True)),
                ("display_phone_number", models.CharField(max_length=40, blank=True)),
                ("encrypted_access_token", models.TextField(blank=True)),
                ("connected", models.BooleanField(default=False)),
                ("notifications_enabled", models.BooleanField(default=False)),
                ("event_settings", models.JSONField(default=dict, blank=True)),
                ("connected_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.OneToOneField(to="companies.company", related_name="whatsapp_connection", on_delete=django.db.models.deletion.CASCADE)),
            ],
        ),
        migrations.CreateModel(
            name="CustomerNotificationPreference",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("whatsapp_opt_in", models.BooleanField(default=False)),
                ("consent_source", models.CharField(max_length=120, blank=True)),
                ("opted_in_at", models.DateTimeField(blank=True, null=True)),
                ("opted_out_at", models.DateTimeField(blank=True, null=True)),
                ("branch", models.ForeignKey(to="branches.branch", blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("customer", models.OneToOneField(to="customers.customer", related_name="notification_preference", on_delete=django.db.models.deletion.CASCADE)),
            ],
        ),
        migrations.CreateModel(
            name="WhatsAppMessageTemplate",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("event", models.CharField(max_length=60)),
                ("template_name", models.CharField(max_length=180)),
                ("language", models.CharField(max_length=24, default="en_US")),
                ("approval_status", models.CharField(max_length=24, default="UNKNOWN")),
                ("branch", models.ForeignKey(to="branches.branch", blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
            ],
        ),
        migrations.CreateModel(
            name="WhatsAppNotificationLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("event", models.CharField(max_length=60)),
                ("idempotency_key", models.CharField(max_length=180)),
                ("phone", models.CharField(max_length=40)),
                ("template_name", models.CharField(max_length=180, blank=True)),
                ("status", models.CharField(max_length=24, default="pending")),
                ("meta_message_id", models.CharField(max_length=180, blank=True, db_index=True)),
                ("error_detail", models.CharField(max_length=500, blank=True)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("branch", models.ForeignKey(to="branches.branch", blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("customer", models.ForeignKey(to="customers.customer", blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL)),
                ("job", models.ForeignKey(to="jobs.job", blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL)),
            ],
            options={"ordering":["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="whatsappmessagetemplate",
            constraint=models.UniqueConstraint(fields=["company", "event"], name="wa_template_company_event_uniq"),
        ),
        migrations.AddConstraint(
            model_name="whatsappnotificationlog",
            constraint=models.UniqueConstraint(fields=["company", "idempotency_key"], name="wa_company_idempotent_uniq"),
        ),
    ]
