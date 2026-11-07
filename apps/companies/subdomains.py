"""Backend-owned, globally unique default website subdomain labels.

The account/workshop login slug is immutable from this workflow. One label
maps to exactly one company; Vercel only needs the platform wildcard once.
"""
import re

from django.db import IntegrityError, transaction
from django.utils.text import slugify

from .models import Company, CompanyWebsiteSubdomain

RESERVED_LABELS = frozenset({
    "www", "api", "admin", "app", "auth", "login", "logout", "panel",
    "dashboard", "billing", "payment", "payments", "support", "help",
    "email", "mail", "smtp", "ftp", "status", "static", "assets",
    "cdn", "root", "portal", "customer", "customers", "account",
    "accounts", "register", "signup", "dns", "vercel", "localhost",
})
LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")


def validate_subdomain_label(value):
    if not isinstance(value, str):
        raise ValueError("Enter a subdomain label.")
    label = value.strip().lower()
    if not LABEL_PATTERN.fullmatch(label) or label in RESERVED_LABELS:
        raise ValueError("Use 3–63 lowercase letters, numbers or hyphens; reserved names are unavailable.")
    return label


def subdomain_label(company):
    return (CompanyWebsiteSubdomain.objects.filter(company_id=company.pk)
            .values_list("label", flat=True).first() or company.slug)


def ensure_default_subdomain(company):
    """Provision a label for existing/admin-created companies without Vercel API calls."""
    current = CompanyWebsiteSubdomain.objects.filter(company_id=company.pk).first()
    if current:
        return current

    base = slugify(company.slug)[:53].strip("-") or "workshop"
    if len(base) < 3 or base in RESERVED_LABELS:
        base = "workshop-" + base
    for counter in range(1, 10000):
        suffix = "" if counter == 1 else "-" + str(counter)
        label = base[:63 - len(suffix)].rstrip("-") + suffix
        if Company.objects.exclude(pk=company.pk).filter(slug=label).exists():
            continue
        if CompanyWebsiteSubdomain.objects.filter(label=label).exists():
            continue
        try:
            with transaction.atomic():
                return CompanyWebsiteSubdomain.objects.create(company=company, label=label)
        except IntegrityError:
            current = CompanyWebsiteSubdomain.objects.filter(company_id=company.pk).first()
            if current:
                return current
    raise ValueError("Unable to allocate a unique workshop subdomain.")


def assign_subdomain(company, requested):
    """Uniqueness enforced by database and protected from login-slug collisions."""
    label = validate_subdomain_label(requested)
    with transaction.atomic():
        company = Company.objects.select_for_update().get(pk=company.pk)
        entry = ensure_default_subdomain(company)
        if entry.label == label:
            return entry
        if Company.objects.exclude(pk=company.pk).filter(slug=label).exists():
            raise ValueError("This subdomain is already reserved by another workshop.")
        if CompanyWebsiteSubdomain.objects.exclude(company=company).filter(label=label).exists():
            raise ValueError("This subdomain is already in use.")
        try:
            # An inner savepoint preserves the outer request if the unique
            # constraint wins a concurrent registration race.
            with transaction.atomic():
                entry.label = label
                entry.save(update_fields=["label", "updated_at"])
        except IntegrityError as exc:
            raise ValueError("This subdomain is already in use.") from exc
        return entry
