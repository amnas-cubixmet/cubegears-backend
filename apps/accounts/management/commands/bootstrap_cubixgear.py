import os
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify
from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.roles.models import Role

class Command(BaseCommand):
    help = "Create the first company, head-office branch, admin role and admin user."

    def handle(self, *args, **options):
        company_name = os.getenv("BOOTSTRAP_COMPANY_NAME", "CubixGear Motors")
        admin_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL")
        admin_password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")

        if not admin_email or not admin_password:
            raise CommandError("Set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD first.")

        company, _ = Company.objects.get_or_create(
            slug=slugify(company_name),
            defaults={"name": company_name, "legal_name": company_name},
        )
        branch, _ = Branch.objects.get_or_create(
            company=company,
            code="HO",
            defaults={"name": "Head Office", "is_head_office": True},
        )
        role, _ = Role.objects.get_or_create(
            company=company,
            code="SUPER_ADMIN",
            defaults={"name": "Super Admin", "permissions": ["*"], "is_system": True},
        )
        user, created = User.objects.get_or_create(
            email=admin_email.lower(),
            defaults={
                "name": "CubixGear Admin",
                "company": company,
                "branch": branch,
                "role": role,
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
                "email_verified": True,
            },
        )
        if created:
            user.set_password(admin_password)
            user.save(update_fields=["password"])

        self.stdout.write(self.style.SUCCESS(f"Bootstrap ready for {admin_email}"))
