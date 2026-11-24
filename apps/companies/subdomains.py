"""Backend-owned, globally unique default website subdomain labels.

The account/workshop login slug is immutable from this workflow. One label
maps to exactly one company; Vercel only needs the platform wildcard once.
"""
import os
import re

from django.conf import settings
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


def customer_website_url(label):
    return f"{settings.CUSTOMER_WEBSITE_SCHEME}://{label}.{customer_portal_root_domain()}/"


def resolve_published_company(label):
    try:
        label = validate_subdomain_label(label)
    except ValueError:
        return None
    entry = CompanyWebsiteSubdomain.objects.select_related("company").filter(label=label).first()
    if entry:
        if entry.website_enabled and entry.company and entry.company.is_active:
            return entry.company
        return None
    # Deliberately limited to legacy/admin-created companies without a reservation.
    return Company.objects.filter(
        slug=label, is_active=True, website_subdomain__isnull=True,
    ).first()


def validate_subdomain_label(value):
    if not isinstance(value, str):
        raise ValueError("Enter a subdomain label.")
    label = value.strip().lower()
    if len(label) < 3 or not LABEL_PATTERN.fullmatch(label) or label in RESERVED_LABELS:
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
    if not base:
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


def check_subdomain_availability(company, requested):
    label = validate_subdomain_label(requested)
    if CompanyWebsiteSubdomain.objects.filter(company=company, label=label).exists():
        return label, True, None
    candidate = next(_available_labels(company, label), None)
    return label, candidate == label, candidate if candidate != label else None


def update_website(company, *, requested=None, website_enabled=None):
    """Serialize address and publication changes for the owning company."""
    with transaction.atomic():
        company = Company.objects.select_for_update().get(pk=company.pk)
        entry = ensure_default_subdomain(company)
        previous = entry.label
        if requested is not None:
            label, available, suggestion = check_subdomain_availability(company, requested)
            if not available:
                raise SubdomainUnavailable("This subdomain is already in use.", suggestion)
            entry.label = label
        if website_enabled is not None:
            entry.website_enabled = website_enabled
        try:
            # A savepoint lets us report conflicts detected by the unique constraint.
            with transaction.atomic():
                entry.save(update_fields=["label", "website_enabled", "updated_at"])
                if entry.label != previous:
                    # Preserve the previous hostname reservation so shared URLs
                    # can never show a different company's branding.
                    CompanyWebsiteSubdomain.objects.create(label=previous, website_enabled=False)
        except IntegrityError as exc:
            _, _, suggestion = check_subdomain_availability(company, entry.label)
            raise SubdomainUnavailable("This subdomain is already in use.", suggestion) from exc
        return entry, previous != entry.label


def assign_subdomain(company, requested):
    entry, _ = update_website(company, requested=validate_subdomain_label(requested))
    return entry
