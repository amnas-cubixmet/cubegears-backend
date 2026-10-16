"""Run once per day on ONE backend instance after configuring storage billing.

Storage is a prepaid-period snapshot of current stored bytes (not historical
byte-hours). The ledger reference prevents double charges on repeated runs.
"""
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from apps.companies.models import Company
from apps.credits.models import StorageCreditPolicy
from apps.credits.storage_billing import charge_storage_period, storage_quote


class Command(BaseCommand):
    help = "Charge wallet credits for stored bytes once per configured period."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        from apps.credits.models import BillingCyclePolicy
        if BillingCyclePolicy.objects.filter(enabled=True).exists():
            self.stdout.write("Calendar month billing is active; no legacy debit was made.")
            return
        from apps.credits.cycle_billing import effective_policy
        if effective_policy():
            raise CommandError("Configurable credit cycles are enabled. Legacy billing is disabled to prevent duplicate charges.")
        if settings.STORAGE_MONTHLY_METERED_ENABLED:
            raise CommandError("Monthly metered storage is enabled. Do NOT run legacy snapshot billing; use settle_metered_storage.")
        policy = StorageCreditPolicy.objects.filter(is_active=True).order_by("pk").first()
        if not policy or policy.billing_mode != "snapshot":
            self.stdout.write("Snapshot billing is disabled. No snapshot charge; metered invoices use monthly settlement.")
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
