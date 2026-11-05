import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("companies", "0004_lock_existing_company_modules")]

    operations = [
        migrations.CreateModel(
            name="CompanyWebsiteSettings",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("public_name", models.CharField(max_length=180, blank=True)),
                ("description", models.CharField(max_length=500, blank=True)),
                ("welcome_message", models.CharField(max_length=240, blank=True)),
                ("contact_phone", models.CharField(max_length=30, blank=True)),
                ("whatsapp_phone", models.CharField(max_length=30, blank=True)),
                ("primary_color", models.CharField(max_length=7, default="#116f69")),
                ("secondary_color", models.CharField(max_length=7, default="#e4f2ec")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.OneToOneField(
                    to="companies.company", related_name="website_settings",
                    on_delete=django.db.models.deletion.CASCADE
                )),
            ]
        ),
        migrations.CreateModel(
            name="CompanyDomain",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("hostname", models.CharField(max_length=253, unique=True, db_index=True)),
                ("status", models.CharField(max_length=30, default="pending_dns", choices=[
                    ("pending_dns", "Pending DNS"),
                    ("pending_verification", "Pending Verification"),
                    ("ssl_pending", "SSL Pending"),
                    ("active", "Active"),
                    ("failed", "Failed"),
                    ("disabled", "Disabled"),
                ])),
                ("is_primary", models.BooleanField(default=False)),
                ("verified_at", models.DateTimeField(null=True, blank=True)),
                ("provider_reference", models.CharField(max_length=180, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(
                    to="companies.company", related_name="customer_domains",
                    on_delete=django.db.models.deletion.CASCADE
                )),
            ]
        ),
    ]
