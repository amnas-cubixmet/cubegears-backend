"""Backfill each existing workshop with a unique public subdomain reservation."""
import re
import django.db.models.deletion
from django.db import migrations, models
from django.utils.text import slugify


RESERVED = {
    "www", "api", "admin", "app", "auth", "login", "logout", "panel",
    "dashboard", "billing", "payment", "payments", "support", "help",
    "email", "mail", "smtp", "ftp", "status", "static", "assets",
    "cdn", "root", "portal", "customer", "customers", "account",
    "accounts", "register", "signup", "dns", "vercel", "localhost",
}


def populate_default_subdomains(apps, schema_editor):
    Company = apps.get_model("companies", "Company")
    Subdomain = apps.get_model("companies", "CompanyWebsiteSubdomain")
    existing_slugs = set(Company.objects.values_list("slug", flat=True))
    assigned = set()
    for company in Company.objects.order_by("created_at", "pk").iterator():
        base = slugify(company.slug)[:53].strip("-") or "workshop"
        if len(base) < 3 or base in RESERVED:
            base = "workshop-" + base
        for index in range(1, 100000):
            suffix = "" if index == 1 else "-" + str(index)
            label = base[:63 - len(suffix)].rstrip("-") + suffix
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", label):
                continue
            if label in assigned or (label in existing_slugs and label != company.slug):
                continue
            Subdomain.objects.create(company_id=company.pk, label=label)
            assigned.add(label)
            break
        else:
            raise RuntimeError("Cannot reserve unique customer website labels.")


class Migration(migrations.Migration):
    dependencies = [("companies", "0008_require_customer_estimate_approval")]

    operations = [
        migrations.CreateModel(
            name="CompanyWebsiteSubdomain",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("label", models.SlugField(max_length=63, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.OneToOneField(
                    to="companies.company", on_delete=django.db.models.deletion.CASCADE,
                    related_name="website_subdomain",
                )),
            ],
        ),
        migrations.RunPython(populate_default_subdomains, migrations.RunPython.noop),
    ]
