"""Diagnose Razorpay backend credentials safely without creating an order.

Usage:
    python manage.py check_razorpay
    python manage.py check_razorpay --probe

Never prints keys, authentication headers, transaction data or remote responses.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.saas.razorpay_client import GatewayError, api_request, configured


class Command(BaseCommand):
    help = "Check Razorpay configuration and optionally verify API authentication (read-only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--probe", action="store_true",
            help="Send a read-only GET to the Razorpay Orders API to verify authentication/network.",
        )

    def handle(self, *args, **options):
        key_id = settings.RAZORPAY_KEY_ID
        secret = settings.RAZORPAY_KEY_SECRET
        mode = (
            "test" if key_id.startswith("rzp_test_")
            else "live" if key_id.startswith("rzp_live_")
            else "unrecognized"
        )
        self.stdout.write(f"Backend .env location: {settings.BASE_DIR / '.env'}")
        self.stdout.write(f"Key ID present: {'yes' if key_id else 'no'}")
        self.stdout.write(f"Key Secret present: {'yes' if secret else 'no'}")
        self.stdout.write(f"Razorpay key mode: {mode}")
        self.stdout.write(
            f"Webhook secret present: {'yes' if settings.RAZORPAY_WEBHOOK_SECRET else 'no'}"
        )
        if not configured():
            raise CommandError(
                "Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to the backend .env "
                "and restart Django. Do not put keys in frontend VITE_* variables."
            )
        if mode == "unrecognized":
            raise CommandError("RAZORPAY_KEY_ID must start with rzp_test_ or rzp_live_.")
        if not options["probe"]:
            self.stdout.write(self.style.SUCCESS(
                "Configuration present. Run with --probe to verify real Razorpay API access."
            ))
            return

        try:
            # GET is safe and makes no purchase. Response data is deliberately discarded.
            api_request("GET", "orders?count=1")
        except GatewayError as exc:
            code = f", HTTP {exc.status_code}" if exc.status_code is not None else ""
            raise CommandError(
                f"Razorpay probe failed: {exc.reason}{code}. "
                "Check key validity/mode and server network; no payment was attempted."
            ) from None
        self.stdout.write(self.style.SUCCESS(
            "Razorpay API authentication OK. No order or payment was created."
        ))
