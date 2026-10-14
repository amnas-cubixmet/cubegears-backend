"""Backfill editable staff role templates without overwriting tenant custom roles."""
from django.core.management.base import BaseCommand, CommandError

from apps.companies.models import Company
from apps.roles.defaults import DEFAULT_STAFF_ROLES, create_default_staff_roles


class Command(BaseCommand):
    help = "Add missing least-privilege staff roles for existing CubixGear workshops."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", help="Optional UUID for one workshop.")
        parser.add_argument("--dry-run", action="store_true", help="Show changes without writing.")

    def handle(self, *args, **options):
        queryset = Company.objects.all().order_by("id")
        if options["company_id"]:
            try:
                queryset = queryset.filter(pk=options["company_id"])
                if not queryset.exists():
                    raise CommandError("Workshop not found.")
            except (ValueError, TypeError) as exc:
                raise CommandError("Invalid workshop UUID.") from exc

        from apps.roles.models import Role
        workshops = 0
        missing_total = 0
        for company in queryset:
            existing_codes = set(Role.objects.filter(company=company).values_list("code", flat=True))
            missing = [code for _, code, _ in DEFAULT_STAFF_ROLES if code not in existing_codes]
            if not missing:
                continue
            workshops += 1
            missing_total += len(missing)
            if not options["dry_run"]:
                create_default_staff_roles(company)
        action = "Would add" if options["dry_run"] else "Created"
        self.stdout.write(self.style.SUCCESS(
            f"{action} {missing_total} missing staff roles across {workshops} workshop(s). "
            "Existing roles and custom permissions were preserved."
        ))
