"""Check Django outbound email configuration without disclosing credentials."""
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.email import email_delivery_mode


class Command(BaseCommand):
    help = "Diagnose account email delivery. Optional --to sends a non-sensitive test email."

    def add_arguments(self, parser):
        parser.add_argument("--to", dest="recipient", default=None,
                            help="Email address to receive a test message (SMTP only).")

    def handle(self, *args, **options):
        mode = email_delivery_mode()
        self.stdout.write(f"Email mode: {mode}")
        self.stdout.write(f"Configured backend: {settings.EMAIL_BACKEND}")
        self.stdout.write(f"Frontend link base: {settings.FRONTEND_URL}")
        if mode != "queued":
            self.stdout.write(self.style.WARNING(
                "This backend is not configured for real inbox delivery. "
                "Set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend in backend .env."
            ))
            if options["recipient"]:
                raise CommandError("Use SMTP to send a real test message.")
            return
        if settings.EMAIL_BACKEND.endswith(".smtp.EmailBackend"):
            if not settings.EMAIL_HOST or not settings.EMAIL_HOST_USER or not settings.EMAIL_HOST_PASSWORD:
                raise CommandError(
                    "SMTP settings are incomplete. Configure EMAIL_HOST, "
                    "EMAIL_HOST_USER and EMAIL_HOST_PASSWORD in private backend .env."
                )
            if settings.EMAIL_USE_SSL and settings.EMAIL_USE_TLS:
                raise CommandError("Enable only one of EMAIL_USE_SSL or EMAIL_USE_TLS.")
        self.stdout.write(self.style.SUCCESS("Email provider configuration is present."))
        if options["recipient"]:
            try:
                sent = send_mail(
                    "CubixGear email delivery test",
                    "CubixGear SMTP configuration test. This contains no login links or passwords.",
                    settings.DEFAULT_FROM_EMAIL,
                    [options["recipient"]],
                    fail_silently=False,
                )
            except Exception as exc:
                raise CommandError(
                    f"Email provider rejected the test message ({type(exc).__name__}). "
                    "Check host, port, app password, sender authorization, and server logs."
                ) from None
            if sent != 1:
                raise CommandError("SMTP provider did not accept the test email.")
            self.stdout.write(self.style.SUCCESS(
                "Email submitted to SMTP provider. Check inbox and spam; this does not guarantee delivery."
            ))
