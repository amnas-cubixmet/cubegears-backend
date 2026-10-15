"""Record opted-in postpaid feature usage; default is read-only."""
from django.core.management.base import BaseCommand
from apps.credits.models import CompanyPostpaidFeature
from apps.credits.monthly_features import meter_all_postpaid_features


class Command(BaseCommand):
    help = "Record observed per-company paid feature time, users and staff."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true")

    def handle(self, *args, **options):
        if not options["execute"]:
            for item in CompanyPostpaidFeature.objects.filter(enabled=True).select_related("feature"):
                self.stdout.write(f"Would measure {item.company_id} / {item.feature.key}")
            return
        self.stdout.write(str(meter_all_postpaid_features()))
