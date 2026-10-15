"""Settle closed storage months from persistent accruals and wallet ledger."""
from django.core.management.base import BaseCommand
from apps.credits.monthly_storage import settle_due_storage


class Command(BaseCommand):
    help = "Preview or charge CLOSED monthly metered storage periods."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true",
                            help="Debit company wallets for closed periods (default is dry-run).")

    def handle(self, *args, **options):
        result = settle_due_storage(dry_run=not options["execute"])
        self.stdout.write(str(result))
