"""Backend-owned, globally unique default website subdomain labels.

The account/workshop login slug is immutable from this workflow. One label
maps to exactly one company; Vercel only needs the platform wildcard once.
"""
import os
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
DEFAULT_PORTAL_ROOT_DOMAIN = "customer.cubixgear.tech"


class SubdomainUnavailable(ValueError):
    def __init__(self, message, suggestion):
        super().__init__(message)
        self.suggestion = suggestion


def customer_portal_root_domain():
    return os.environ.get("CUSTOMER_PORTAL_ROOT_DOMAIN", DEFAULT_PORTAL_ROOT_DOMAIN).strip().lower().strip(".")


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


def _available_labels(company, base):
    for counter in range(10000):
        suffix = str(counter) if counter else ""
        label = base[:63 - len(suffix)].rstrip("-") + suffix
        if Company.objects.exclude(pk=company.pk).filter(slug=label).exists():
            continue
        if CompanyWebsiteSubdomain.objects.filter(label=label).exists():
            continue
        yield label


def ensure_default_subdomain(company):
    """Provision a label for existing/admin-created companies without Vercel API calls."""
    current = CompanyWebsiteSubdomain.objects.filter(company_id=company.pk).first()
    if current:
        return current

    base = re.sub(r"[-_]+", "-", slugify(company.name))[:53].strip("-")
    if not re.search(r"[a-z]", base):
        base = "workshop"
    if len(base) < 3 or base in RESERVED_LABELS:
        base = "workshop-" + base
    for label in _available_labels(company, base):
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
        available = next(_available_labels(company, label), None)
        if available != label:
            raise SubdomainUnavailable("This subdomain is already in use.", available)
        try:
            # An inner savepoint preserves the outer request if the unique
            # constraint wins a concurrent registration race.
            with transaction.atomic():
                entry.label = label
                entry.save(update_fields=["label", "updated_at"])
        except IntegrityError as exc:
            suggestion = next(_available_labels(company, label), None)
            raise SubdomainUnavailable("This subdomain is already in use.", suggestion) from exc
        return entry
