"""Run due read-only storage usage snapshots, independent of billing."""
from django.core.management.base import BaseCommand
from apps.credits.storage_monitoring import snapshot_due_storage


class Command(BaseCommand):
    help = "Take configured storage usage snapshots (no wallet debit)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true",
            help="Refresh now even if the observation interval has not elapsed.",
        )

    def handle(self, *args, **options):
        result = snapshot_due_storage(force=options["force"])
        self.stdout.write(f"Storage monitoring: {result['status']} ({result['companies']} companies)")
