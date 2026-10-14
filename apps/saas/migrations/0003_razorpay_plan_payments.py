import uuid
import django.db.models.deletion
from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):
    dependencies = [
        ("saas", "0002_dynamic_plans"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriptionplan",
            name="access_duration_days",
            field=models.PositiveIntegerField(
                default=180,
                help_text="Days of access after each verified payment (e.g. 180 or 210).",
            ),
        ),
        migrations.CreateModel(
            name="RazorpayPlanPayment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("cycle", models.CharField(max_length=10, choices=[("monthly", "Monthly"), ("yearly", "Yearly")])),
                ("validity_days", models.PositiveIntegerField()),
                ("amount_paise", models.PositiveBigIntegerField()),
                ("currency", models.CharField(default="INR", max_length=3)),
                ("receipt", models.CharField(max_length=40, unique=True)),
                ("razorpay_order_id", models.CharField(blank=True, max_length=80, null=True, unique=True)),
                ("razorpay_payment_id", models.CharField(blank=True, max_length=80, null=True, unique=True)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("captured", "Captured"), ("failed", "Failed"), ("refunded", "Refunded")], db_index=True, default="pending", max_length=15)),
                ("payment_method", models.CharField(blank=True, max_length=30)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("period_start", models.DateTimeField(blank=True, null=True)),
                ("period_end", models.DateTimeField(blank=True, null=True)),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="branches.branch")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="companies.company")),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="razorpay_payments", to="saas.subscriptionplan")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("subscription", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="razorpay_payments", to="saas.subscription")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["company", "status"], name="saas_rp_company_status")],
            },
        ),
        migrations.CreateModel(
            name="RazorpayWebhookDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.CharField(max_length=150, unique=True)),
                ("event_type", models.CharField(max_length=100)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
