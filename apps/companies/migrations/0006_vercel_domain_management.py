import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("companies", "0005_customer_website")]

    operations = [
        migrations.AddField(
            model_name="companydomain", name="dns_provider",
            field=models.CharField(max_length=24, default="other", choices=[
                ("godaddy", "GoDaddy"), ("cloudflare", "Cloudflare"),
                ("hostinger", "Hostinger"), ("namecheap", "Namecheap"),
                ("vercel", "Vercel DNS"), ("other", "Other"),
            ]),
        ),
        migrations.AddField(model_name="companydomain", name="dns_records", field=models.JSONField(default=list, blank=True)),
        migrations.AddField(model_name="companydomain", name="verification_records", field=models.JSONField(default=list, blank=True)),
        migrations.AddField(model_name="companydomain", name="dns_configured", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="companydomain", name="ownership_verified", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="companydomain", name="ssl_status", field=models.CharField(max_length=16, default="pending")),
        migrations.AddField(model_name="companydomain", name="last_error", field=models.CharField(max_length=500, blank=True)),
        migrations.AddField(model_name="companydomain", name="last_checked_at", field=models.DateTimeField(null=True, blank=True)),
        migrations.AddField(model_name="companydomain", name="is_approved", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="companydomain", name="approved_by", field=models.ForeignKey(
            "accounts.User", null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL,
            related_name="approved_customer_domains",
        )),
        migrations.AddField(model_name="companydomain", name="vercel_project_id", field=models.CharField(max_length=100, blank=True)),
        migrations.AlterField(model_name="companydomain", name="status", field=models.CharField(
            max_length=30, default="pending_dns", choices=[
                ("pending_setup", "Pending Setup"), ("pending_dns", "Pending DNS"),
                ("pending_verification", "Pending Verification"), ("ssl_pending", "SSL Pending"),
                ("active", "Active"), ("failed", "Failed"), ("disabled", "Disabled"),
            ],
        )),
        migrations.CreateModel(
            name="CompanyDomainAudit",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("action", models.CharField(max_length=40)),
                ("details", models.JSONField(default=dict, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(
                    "accounts.User", on_delete=django.db.models.deletion.SET_NULL,
                    null=True, blank=True,
                )),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("domain", models.ForeignKey(
                    to="companies.companydomain", on_delete=django.db.models.deletion.CASCADE,
                    related_name="events",
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="companydomain",
            constraint=models.UniqueConstraint(
                fields=["company"], condition=models.Q(is_primary=True),
                name="one_primary_website_domain_per_company",
            ),
        ),
    ]
