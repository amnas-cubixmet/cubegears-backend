"""Manually preview or settle opted-in monthly cycles (one worker/operator)."""
from django.core.management.base import BaseCommand
from apps.credits.cycle_billing import run_due_cycles


class Command(BaseCommand):
    help = "Preview/settle 1–28 day-of-month credit cycles using only server-observed usage."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Read-only quote; do not invoice or debit.")

    def handle(self, *args, **options):
        results = run_due_cycles(dry_run=options["dry_run"])
        for status, count in sorted(results.items()):
            self.stdout.write(f"{status}: {count}")
        if options["dry_run"]:
            self.stdout.write("DRY RUN: no credit balance or billing records changed.")
