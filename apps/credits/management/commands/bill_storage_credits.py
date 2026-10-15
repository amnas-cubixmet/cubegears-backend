"""Run once per day on ONE backend instance after configuring storage billing.

Storage is a prepaid-period snapshot of current stored bytes (not historical
byte-hours). The ledger reference prevents double charges on repeated runs.
"""
from django.core.management.base import BaseCommand
from apps.companies.models import Company
from apps.credits.models import StorageCreditPolicy
from apps.credits.storage_billing import charge_storage_period, storage_quote


class Command(BaseCommand):
    help = "Charge wallet credits for stored bytes once per configured period."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        policy = StorageCreditPolicy.objects.filter(is_active=True).order_by("pk").first()
        if not policy:
            self.stdout.write("Storage billing is disabled. Nothing charged.")
            return
        totals = {"charged": 0, "already_billed": 0, "insufficient_credits": 0}
        for company in Company.objects.filter(is_active=True).iterator():
            if options["dry_run"]:
                quote = storage_quote(company, policy)
                self.stdout.write(
                    f"Would bill company {company.pk}: {quote['used_gb']} GB "
                    f"-> {quote['credits']} credits for {quote['period']}"
                )
                continue
            state, quote = charge_storage_period(company, policy)
            if state in totals:
                totals[state] += 1
            if state == "insufficient_credits":
                self.stderr.write(
                    f"Company {company.pk}: insufficient credits for storage period {quote['period']}"
                )
        self.stdout.write(f"Storage billing: {totals}")
