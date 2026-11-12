"""Local operator-only login diagnostics, without exposing credentials or tokens.

Usage:
    python manage.py diagnose_login --identifier owner@example.com --check-password
    python manage.py diagnose_login --identifier +919876543210 --workshop garage

Run this only in the environment that serves the failing /auth/login request.
Never paste an entered password, private token, or credential into chat/logs.
"""
from getpass import getpass

from django.contrib.auth import authenticate
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.phone import normalize_phone
from apps.accounts.serializers import user_can_sign_in


class Command(BaseCommand):
    help = (
        "Diagnose login for a local account: password setup, active account, "
        "workshop/role and optional interactive password check. No secrets printed."
    )

    def add_arguments(self, parser):
        parser.add_argument("--identifier", required=True, help="Email or international mobile number")
        parser.add_argument("--workshop", default="", help="Optional workshop slug for shared identities")
        parser.add_argument(
            "--check-password", action="store_true",
            help="Prompt privately for the password and check it against this database",
        )

    def handle(self, *args, **options):
        ident = options["identifier"].strip()
        workshop = options["workshop"].strip().lower()
        if not ident:
            raise CommandError("Provide an email or international mobile number.")
        if "@" in ident:
            candidates = User.objects.filter(email__iexact=ident)
            mode = "email"
        else:
            try:
                mobile = normalize_phone(ident)
            except Exception as exc:
                raise CommandError("Mobile must include a valid international country code.") from exc
            candidates = User.objects.filter(normalized_phone=mobile)
            mode = "mobile"
        if workshop:
            candidates = candidates.filter(company__slug=workshop)
        accounts = list(candidates.select_related("company", "role")[:51])
        self.stdout.write("Diagnostic uses this Django process's configured database.")
        if not accounts:
            self.stdout.write(self.style.WARNING(
                "No matching account in this database/workshop. Check the backend "
                "environment, database, registered identifier and workshop code."
            ))
            return
        if len(accounts) > 50:
            raise CommandError("Too many matching records. Supply --workshop to narrow the search.")
        if len(accounts) > 1 and not workshop:
            self.stdout.write(self.style.WARNING(
                "Multiple matching accounts. Supply --workshop with the correct "
                "workshop slug when signing in."
            ))

        entered_password = None
        if options["check_password"]:
            entered_password = getpass("Password (hidden, never printed): ")

        for account in accounts:
            company = account.company
            role = account.role
            try:
                employee = account.employee_profile
            except Exception:
                employee = None
            owner = bool(company and company.owner_id == account.pk)
            self.stdout.write("--- Account state ---")
            self.stdout.write(f"Workshop code: {company.slug if company else '(platform account)'}")
            self.stdout.write(f"Account type: {'owner' if owner else 'staff/platform'}")
            self.stdout.write(f"Password configured: {account.has_usable_password()}")
            self.stdout.write(f"Account active: {account.is_active}")
            self.stdout.write(f"Workshop active: {company.is_active if company else 'not applicable'}")
            self.stdout.write(f"Role assigned and active: {bool(role and role.is_active and (not company or role.company_id == company.pk))}")
            self.stdout.write(f"Employee status: {employee.status if employee else 'not linked'}")
            recent = account.staff_password_invitations.order_by("-created_at").first()
            if recent:
                invite_status = (
                    "used" if recent.used_at else
                    "replaced" if recent.invalidated_at else
                    "expired" if recent.expires_at <= timezone.now() else
                    "pending"
                )
                self.stdout.write(f"Most recent password link: {invite_status}")
            self.stdout.write(f"Account eligible for sign-in: {user_can_sign_in(account)}")

            if entered_password is not None:
                matches = account.check_password(entered_password)
                self.stdout.write(f"Password matches this database: {matches}")
                if matches and mode == "email":
                    accepted = authenticate(email=account.email, password=entered_password)
                    self.stdout.write(f"Django email authentication accepted: {accepted is not None}")
                if matches and not user_can_sign_in(account):
                    self.stdout.write(self.style.WARNING(
                        "Password matches, but the account/role/workshop is not eligible for sign-in."
                    ))
                elif not matches:
                    self.stdout.write(self.style.WARNING(
                        "The stored password hash does not match this entry. "
                        "Verify you are using the backend database where setup completed, "
                        "or request a new password reset link."
                    ))
            if not account.has_usable_password():
                self.stdout.write(self.style.WARNING(
                    "Password setup is pending. Finish a valid one-time setup link first."
                ))
