"""Daily captured-subscription expiry reminders; configure one cron/systemd timer."""
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.timezone import localdate

from apps.saas.models import Subscription, SubscriptionExpiryReminder


class Command(BaseCommand):
    help = "Email company billing contacts before their verified subscription expires."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Report planned notices without sending.")

    def handle(self, *args, **options):
        today = localdate()
        now = timezone.now()
        sent = 0
        subscriptions = Subscription.objects.filter(
            status__iexact="Active", razorpay_payments__status="captured",
            renews_at__gt=now,
        ).select_related("company").distinct()
        for sub in subscriptions.iterator():
            remaining = (localdate(sub.renews_at) - today).days
            if remaining not in (30, 7, 1, 0):
                continue
            recipient = (sub.company.email or "").strip()
            if not recipient:
                self.stdout.write(f"Skipping company {sub.company_id}: no email")
                continue
            if SubscriptionExpiryReminder.objects.filter(
                subscription=sub, days_before=remaining,
            ).exists():
                continue
            if options["dry_run"]:
                self.stdout.write(f"Would send {remaining}-day reminder to company {sub.company_id}")
                continue
            with transaction.atomic():
                notice, created = SubscriptionExpiryReminder.objects.get_or_create(
                    subscription=sub, days_before=remaining,
                    defaults={"email": recipient},
                )
            if not created:
                continue
            url = settings.FRONTEND_URL.rstrip("/") + "/subscribe"
            message = (
                f"Your CubeGear plan '{sub.plan}' expires on "
                f"{localdate(sub.renews_at):%d %b %Y}.\n"
                f"Remaining: {remaining} day(s).\n"
                f"Please sign in and renew via {url}. "
                "Only a verified, captured Razorpay payment extends your access."
            )
            try:
                accepted = send_mail(
                    "CubeGear subscription renewal reminder", message,
                    settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False,
                )
                if accepted != 1:
                    raise RuntimeError("Mail provider accepted no messages.")
            except Exception as exc:
                SubscriptionExpiryReminder.objects.filter(pk=notice.pk).delete()
                self.stderr.write(f"Renewal email failed for subscription {sub.pk}: {type(exc).__name__}")
                continue
            sent += 1
        self.stdout.write(self.style.SUCCESS(f"Renewal reminders submitted: {sent}"))
