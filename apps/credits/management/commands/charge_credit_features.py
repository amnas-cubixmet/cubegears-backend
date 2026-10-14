"""Run daily from ONE scheduled instance. Charges enabled features every 30 days."""
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.credits.models import CompanyCreditFeature
from apps.credits.service import renew_feature


class Command(BaseCommand):
    help = "Renew 30-day credit features or suspend them when balance is insufficient."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        qs = CompanyCreditFeature.objects.filter(
            enabled=True, next_charge_at__lte=timezone.now(),
        ).select_related("company", "feature")
        totals = {"renewed": 0, "suspended": 0, "unavailable": 0}
        for state in qs.iterator():
            if options["dry_run"]:
                self.stdout.write(f"Would charge {state.company_id} {state.feature.key} {state.pk}")
                continue
            status = renew_feature(state)
            if status in totals:
                totals[status] += 1
        self.stdout.write(self.style.SUCCESS(f"Credit renewal result: {totals}"))
