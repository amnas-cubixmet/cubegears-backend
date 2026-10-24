"""Check account-email configuration; --to sends a harmless test message."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from apps.accounts.email import email_delivery_mode, queue_email


class Command(BaseCommand):
    help = "Validate email mode and optionally send or queue a non-sensitive test email."

    def add_arguments(self, parser):
        parser.add_argument("--to", dest="recipient", default=None)

    def handle(self, *args, **options):
        mode = email_delivery_mode()
        self.stdout.write(f"Email dispatch readiness: {mode}")
        self.stdout.write(f"Email backend: {settings.EMAIL_BACKEND}")
        self.stdout.write(f"Frontend link base: {settings.FRONTEND_URL}")
        if mode == "disabled":
            message = (
                "Account email disabled or incomplete. Configure ACCOUNT_EMAIL_DELIVERY_MODE=smtp "
                "plus SMTP host, sender and credentials; or configure Celery + Redis."
            )
            if options["recipient"]:
                raise CommandError(message)
            self.stdout.write(self.style.WARNING(message))
            return
        if settings.EMAIL_BACKEND != "django.core.mail.backends.smtp.EmailBackend":
            raise CommandError("Only configured SMTP email is supported for real delivery checks.")
        if not all((settings.EMAIL_HOST, settings.EMAIL_HOST_USER,
                    settings.EMAIL_HOST_PASSWORD, settings.DEFAULT_FROM_EMAIL)):
            raise CommandError("Configure SMTP host, sender and credentials privately.")
        if settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL:
            raise CommandError("Only one of EMAIL_USE_TLS and EMAIL_USE_SSL may be enabled.")
        self.stdout.write("SMTP settings are present; actual inbox delivery is not yet verified.")
        if options["recipient"]:
            try:
                validate_email(options["recipient"])
            except ValidationError:
                raise CommandError("Enter a valid recipient email.") from None
            result = queue_email(
                "CubixGear email delivery test",
                "Email delivery test. No login links or passwords are included.",
                [options["recipient"]],
            )
            if result["status"] == "pending":
                self.stdout.write("Test is pending DB transaction commit.")
            elif result["status"] == "sent":
                self.stdout.write(self.style.SUCCESS("SMTP accepted the test email; check inbox and spam."))
            elif result["status"] == "queued":
                self.stdout.write(self.style.SUCCESS("Test queued to Redis; confirm worker and inbox."))
            else:
                raise CommandError("Email dispatch failed; check host networking and mail settings.")
