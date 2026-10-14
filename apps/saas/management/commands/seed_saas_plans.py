"""Create an editable starter catalog without overwriting platform price changes."""
from django.core.management.base import BaseCommand
from apps.saas.models import FeatureGroup, PlanFeature, SubscriptionPlan, PlanFeatureAccess


CATEGORIES = [
    ("core", "Workshop Essentials", [
        ("dashboard", "Basic Dashboard"),
        ("customers", "Customer Management"),
        ("vehicles", "Vehicle Management"),
        ("jobs", "Job Card Management"),
        ("services", "Service Catalogue"),
    ]),
    ("operations", "Workshop Operations", [
        ("stock", "Stock & Inventory"),
        ("staff", "Staff Management"),
        ("attendance", "Attendance & Punches"),
        ("payroll", "Daily Wage Payroll"),
    ]),
    ("analytics", "Reports & Finance", [
        ("reports", "Reports & Analytics"),
        ("payments", "Payment Tracking"),
        ("expenses", "Expenses"),
        ("notifications", "Notifications"),
    ]),
]

PLANS = [
    ("starter", "Starter", 799, 7990, 1, 3, 5,
     {"dashboard", "customers", "vehicles", "jobs", "services", "payments"}),
    ("professional", "Professional", 1499, 14990, 1, 10, 20,
     {"dashboard", "customers", "vehicles", "jobs", "services", "payments",
      "stock", "staff", "attendance", "payroll", "reports", "expenses", "notifications"}),
    ("business", "Business", 2999, 29990, 3, 25, 50,
     {"dashboard", "customers", "vehicles", "jobs", "services", "payments",
      "stock", "staff", "attendance", "payroll", "reports", "expenses", "notifications"}),
]


class Command(BaseCommand):
    help = "Seed the first editable SaaS feature and plan catalog (existing changes are preserved)."

    def handle(self, *args, **kwargs):
        for group_key, group_label, features in CATEGORIES:
            group, _ = FeatureGroup.objects.get_or_create(
                key=group_key, defaults={"label": group_label})
            for key, label in features:
                PlanFeature.objects.get_or_create(
                    key=key, defaults={"label": label, "group": group})

        for key, name, monthly, yearly, branches, users, storage, allowed in PLANS:
            plan, created = SubscriptionPlan.objects.get_or_create(
                key=key, defaults={
                    "name": name, "monthly_price": monthly,
                    "yearly_price": yearly, "max_branches": branches,
                    "max_users": users, "included_storage_gb": storage,
                    "extra_storage_rate": 15, "trial_days": 14,
                }
            )
            if created:
                PlanFeatureAccess.objects.bulk_create([
                    PlanFeatureAccess(plan=plan, feature=feature,
                                      enabled=feature.key in allowed)
                    for feature in PlanFeature.objects.filter(is_active=True)
                ])
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if created else 'Kept'} {name} plan"))
        self.stdout.write("GST invoicing is available on every plan.")
