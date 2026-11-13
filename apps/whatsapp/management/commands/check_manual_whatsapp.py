"""Read-only production preflight for manual WhatsApp sharing.

Does not send messages, generate bearer links, or print credentials.
"""
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Check manual WhatsApp dependencies without exposing secrets"

    def handle(self, *args, **options):
        from apps.accounts.models import StaffPasswordInvitation
        from apps.customers.models import CustomerPortalAccess
        from apps.whatsapp.models import WhatsAppNotificationLog

        problems = []
        frontend = urlsplit(str(getattr(settings, "FRONTEND_URL", "")))
        portal = urlsplit(str(getattr(settings, "CUSTOMER_PORTAL_BASE_URL", "")))
        if not settings.DEBUG:
            if frontend.scheme != "https" or not frontend.hostname:
                problems.append("FRONTEND_URL must be an HTTPS company-panel URL")
            if portal.scheme != "https" or not portal.hostname:
                problems.append("CUSTOMER_PORTAL_BASE_URL must be an HTTPS customer URL")
        if frontend.hostname in ("localhost", "127.0.0.1") and not settings.DEBUG:
            problems.append("FRONTEND_URL cannot point to localhost in production")
        if portal.hostname in ("localhost", "127.0.0.1") and not settings.DEBUG:
            problems.append("CUSTOMER_PORTAL_BASE_URL cannot point to localhost in production")

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            existing = set(connection.introspection.table_names())
        except Exception as exc:
            raise CommandError("Database connection unavailable. Check DB_* settings and hosting logs.") from exc

        for model in (StaffPasswordInvitation, CustomerPortalAccess, WhatsAppNotificationLog):
            if model._meta.db_table not in existing:
                problems.append("Missing database table: " + model._meta.db_table + " (run migrations)")

        if problems:
            for issue in problems:
                self.stderr.write(self.style.ERROR(issue))
            raise CommandError("Manual WhatsApp readiness check failed")
        self.stdout.write(self.style.SUCCESS("Manual WhatsApp prerequisites OK (not a live API/send test)."))
