"""Settle due postpaid feature periods; default is safe dry-run."""
from django.core.management.base import BaseCommand
from apps.credits.monthly_features import settle_due_postpaid_features


class Command(BaseCommand):
    help = "Charge wallet credits for observed CLOSED feature billing months."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true")

    def handle(self, *args, **options):
        self.stdout.write(str(settle_due_postpaid_features(dry_run=not options["execute"])))
