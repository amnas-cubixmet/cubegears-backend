"""Publish integrated workshop workflow packages without touching legacy balances.

The new products are a single paid feature EACH, with one credit quote per
user rather than the sum of their modules. Previous subscriptions, purchases,
ledger snapshots, price edits and metered intervals remain unchanged.
"""
from decimal import Decimal
from django.db import migrations

PACKAGES = (
    ("workshop_operations", "Workshop Operations",
     "Customers, vehicles, services, job cards, inventory, invoices, updates."),
    ("accounts_tax", "Accounts & Tax",
     "Payments, expenses, accounts and GST."),
    ("attendance_payroll", "Attendance & Payroll",
     "Shifts, attendance, leave, overtime, daily wages and payroll."),
    ("business_insights", "Reports & Analytics",
     "Workshop data reports and dashboards."),
)


def publish_grouped_products(apps, schema_editor):
    alias = schema_editor.connection.alias
    FeatureGroup = apps.get_model("saas", "FeatureGroup")
    PlanFeature = apps.get_model("saas", "PlanFeature")
    FeatureBundle = apps.get_model("credits", "FeatureBundle")
    ModulePriceLabel = apps.get_model("credits", "ModulePriceLabel")
    group, _ = FeatureGroup.objects.using(alias).get_or_create(
        key="workflow-packages",
        defaults={"label": "Integrated Workshop Features", "sort_order": 1},
    )
    for key, label, description in PACKAGES:
        feature, _ = PlanFeature.objects.using(alias).get_or_create(
            key=key, defaults={"label": label, "description": description,
                               "group_id": group.pk, "is_active": True},
        )
        if ModulePriceLabel.objects.using(alias).filter(feature_id=feature.pk).exists():
            # Never reset an administrator's price or billing model.
            continue
        bundle, _ = FeatureBundle.objects.using(alias).get_or_create(
            key="workflow-" + key,
            defaults={
                "name": label, "description": description,
                "per_user_credits": Decimal("5.00"),
                "price_credits": Decimal("0.00"),
                "activation_credits": Decimal("0.00"),
                "billing_type": "monthly", "is_active": True,
                "sort_order": 10,
            },
        )
        bundle.features.add(feature)
        ModulePriceLabel.objects.using(alias).get_or_create(
            feature_id=feature.pk, defaults={"bundle_id": bundle.pk,
                                             "monthly_postpaid": False},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0024_calendar_payment_due_window"),
        ("saas", "0009_platform_audit_company_nullable"),
    ]
    operations = [
        migrations.RunPython(publish_grouped_products, migrations.RunPython.noop),
    ]
