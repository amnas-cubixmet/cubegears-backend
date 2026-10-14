import uuid
from decimal import Decimal
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("saas", "0006_merge_storage_checkout"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(name="CreditPackage", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("name", models.CharField(max_length=120)),
            ("credits", models.DecimalField(decimal_places=2, max_digits=12)),
            ("price_inr", models.DecimalField(decimal_places=2, max_digits=12)),
            ("is_active", models.BooleanField(default=True)),
            ("sort_order", models.PositiveIntegerField(default=0)),
        ], options={"ordering": ["sort_order", "id"]}),
        migrations.CreateModel(name="CreditWallet", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("balance", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=16)),
            ("low_threshold", models.DecimalField(decimal_places=2, default=Decimal("100.00"), max_digits=12)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("company", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="credit_wallet", to="companies.company")),
        ]),
        migrations.CreateModel(name="CreditWebhookEvent", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("event_id", models.CharField(max_length=150, unique=True)),
            ("event_type", models.CharField(max_length=100)),
            ("received_at", models.DateTimeField(auto_now_add=True)),
        ]),
        migrations.CreateModel(name="FeatureCreditPrice", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("base_credits", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
            ("per_staff_credits", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
            ("is_available", models.BooleanField(default=True)),
            ("feature", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="credit_price", to="saas.planfeature")),
        ]),
        migrations.CreateModel(name="CompanyCreditFeature", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("enabled", models.BooleanField(default=False)),
            ("next_charge_at", models.DateTimeField(blank=True, null=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="credit_features", to="companies.company")),
            ("feature", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="saas.planfeature")),
        ]),
        migrations.CreateModel(name="CreditLedger", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("kind", models.CharField(db_index=True, max_length=20)),
            ("description", models.CharField(max_length=255)),
            ("delta", models.DecimalField(decimal_places=2, max_digits=16)),
            ("balance_after", models.DecimalField(decimal_places=2, max_digits=16)),
            ("reference", models.CharField(max_length=180, unique=True)),
            ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="credit_events", to="companies.company")),
        ], options={"ordering": ["-created_at", "-id"], "indexes": [models.Index(fields=["company", "created_at"], name="credit_ledger_company_dt")]}),
        migrations.CreateModel(name="CreditPurchase", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("credits", models.DecimalField(decimal_places=2, max_digits=12)),
            ("amount_paise", models.PositiveBigIntegerField()),
            ("status", models.CharField(db_index=True, default="pending", max_length=16)),
            ("receipt", models.CharField(max_length=40, unique=True)),
            ("order_id", models.CharField(blank=True, max_length=80, null=True, unique=True)),
            ("payment_id", models.CharField(blank=True, max_length=80, null=True, unique=True)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("paid_at", models.DateTimeField(blank=True, null=True)),
            ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="credit_purchases", to="companies.company")),
            ("package", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="credits.creditpackage")),
            ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.AddConstraint(model_name="companycreditfeature", constraint=models.UniqueConstraint(fields=("company", "feature"), name="credits_unique_company_feature")),
    ]
