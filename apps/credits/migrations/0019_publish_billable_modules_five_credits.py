"""Publish the thirteen currently implemented billable modules at five credits/user.

This is a one-time platform catalog price reset, not a tenant activation,
wallet debit, purchase creation, or rewrite of historical invoice snapshots.
Existing purchased billing periods and postpaid/prepaid mode stay unchanged.
"""

from decimal import Decimal
from uuid import uuid4

from django.db import migrations


# Only modules backed by tenant permissions. Free/system modules are excluded.
BILLABLE_MODULES = (
    ("attendance", "Attendance & Punches"),
    ("customers", "Customer Management"),
    ("expenses", "Expense Tracking"),
    ("invoices", "Invoices & Estimates"),
    ("jobs", "Job Cards"),
    ("notifications", "Notifications"),
    ("payments", "Payment Tracking"),
    ("payroll", "Payroll"),
    ("reports", "Reports & Analytics"),
    ("services", "Service Catalogue"),
    ("stock", "Stock & Inventory"),
    ("storage", "Storage"),
    ("vehicles", "Vehicle Management"),
)
CREDITS_PER_USER = Decimal("5.00")
ZERO = Decimal("0.00")


def publish_five_credit_modules(apps, schema_editor):
    FeatureGroup = apps.get_model("saas", "FeatureGroup")
    PlanFeature = apps.get_model("saas", "PlanFeature")
    FeatureBundle = apps.get_model("credits", "FeatureBundle")
    ModulePriceLabel = apps.get_model("credits", "ModulePriceLabel")
    CompanyBundleAccess = apps.get_model("credits", "CompanyBundleAccess")
    FeatureCreditPrice = apps.get_model("credits", "FeatureCreditPrice")
    database = schema_editor.connection.alias

    group, _ = FeatureGroup.objects.using(database).get_or_create(
        key="module-prices",
        defaults={"label": "Module Pricing", "is_active": True},
    )
    if not group.is_active:
        FeatureGroup.objects.using(database).filter(pk=group.pk).update(is_active=True)

    for key, label in BILLABLE_MODULES:
        feature, _ = PlanFeature.objects.using(database).get_or_create(
            key=key, defaults={"label": label, "group_id": group.pk, "is_active": True},
        )
        if not feature.is_active:
            PlanFeature.objects.using(database).filter(pk=feature.pk).update(is_active=True)

        mapping = ModulePriceLabel.objects.using(database).filter(feature_id=feature.pk).first()
        if mapping:
            bundle = FeatureBundle.objects.using(database).get(pk=mapping.bundle_id)
            # Existing prepaid purchases retain their original billing period.
            has_purchases = CompanyBundleAccess.objects.using(database).filter(bundle_id=bundle.pk).exists()
            bundle.per_user_credits = CREDITS_PER_USER
            bundle.is_active = True
            bundle.price_credits = ZERO
            bundle.activation_credits = ZERO
            update_fields = ["per_user_credits", "is_active", "price_credits", "activation_credits"]
            if not has_purchases and not mapping.monthly_postpaid:
                bundle.billing_type = "monthly"
                update_fields.append("billing_type")
            bundle.save(using=database, update_fields=update_fields)
        else:
            bundle = FeatureBundle.objects.using(database).create(
                key="module-" + uuid4().hex,
                name=feature.label,
                per_user_credits=CREDITS_PER_USER,
                billing_type="monthly",
                price_credits=ZERO,
                activation_credits=ZERO,
                is_active=True,
            )
            bundle.features.add(feature)
            ModulePriceLabel.objects.using(database).create(
                feature_id=feature.pk,
                bundle_id=bundle.pk,
                monthly_postpaid=False,
            )
            continue

        # Existing postpaid mode must be preserved; update the future price,
        # never a past reading, invoice, liability, wallet, or entitlement.
        if mapping.monthly_postpaid:
            FeatureCreditPrice.objects.using(database).update_or_create(
                feature_id=feature.pk,
                defaults={
                    "base_credits": ZERO,
                    "per_staff_credits": ZERO,
                    "activation_credits": ZERO,
                    "per_user_credits": CREDITS_PER_USER,
                    "billing_period": "monthly",
                    "is_available": True,
                },
            )


class Migration(migrations.Migration):
    dependencies = [
        ("credits", "0018_creditpackage_is_archived"),
    ]

    operations = [
        migrations.RunPython(publish_five_credit_modules, migrations.RunPython.noop),
    ]
