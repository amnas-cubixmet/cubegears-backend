"""Scheduled-task alternative when a host does not support Celery workers."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.attendance.tasks import auto_close_attendance_sessions


class Command(BaseCommand):
    help = "Run the existing attendance auto-checkout safely from a host scheduler."

    def add_arguments(self, parser):
        parser.add_argument("--run", action="store_true",
                            help="Explicitly execute attendance updates; otherwise report only.")

    def handle(self, *args, **options):
        if not settings.ATTENDANCE_AUTO_CHECKOUT_ENABLED:
            raise CommandError(
                "ATTENDANCE_AUTO_CHECKOUT_ENABLED=False; no attendance was changed."
            )
        if not options["run"]:
            self.stdout.write("Attendance automation enabled. No changes made; pass --run to execute.")
            return
        # Execute locally; DO NOT use .delay() (a Redis worker is not needed).
        result = auto_close_attendance_sessions.run()
        self.stdout.write(self.style.SUCCESS(f"Attendance auto-checkout: {result}"))
