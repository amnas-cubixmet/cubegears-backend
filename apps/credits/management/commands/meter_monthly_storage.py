"""Manually record company storage readings using real file bytes."""
from django.core.management.base import BaseCommand
from apps.credits.monthly_storage import _policy, meter_all_company_storage
from apps.companies.models import Company
from apps.credits.storage_billing import storage_usage


class Command(BaseCommand):
    help = "Show or record real company storage readings for monthly billing."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true",
                            help="Persist readings (default is safe read-only preview).")

    def handle(self, *args, **options):
        policy = _policy()
        if policy is None:
            self.stdout.write("Monthly storage metering is disabled; no records changed.")
            return
        if not options["execute"]:
            for company in Company.objects.filter(is_active=True).iterator():
                self.stdout.write(f"Would sample {company.pk}: {storage_usage(company)} actual bytes")
            return
        self.stdout.write(str(meter_all_company_storage()))
