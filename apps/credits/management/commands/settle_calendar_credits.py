"""Idempotent calendar month closing and due-day settlement (daily by Celery)."""
from django.core.management.base import BaseCommand
from apps.credits.calendar_billing import run_calendar_cycles


class Command(BaseCommand):
    help = "Finalize closed calendar usage periods and settle complete statements on the configured due day."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        report = run_calendar_cycles(dry_run=options["dry_run"])
        self.stdout.write(str(report))
