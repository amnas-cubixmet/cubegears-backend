"""Validated tenant invoice/estimate appearance settings shared by UI and PDF.

This is styling configuration, not a way to override legal GST identity or totals.
Never trust client-requested prices or untrusted external image URLs in PDF.
"""
import base64
import binascii
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework import serializers
from apps.saas.models import CompanySetting
from apps.inventory.models import WorkshopFinancialAccount

STYLE_DEFAULTS = {
    "invoiceTemplate": "modern", "estimateTemplate": "modern",
    "accent": "teal", "showOriginal": True, "showSellerMark": True,
    "compactTable": False, "customName": "",
    "customHeader": "", "customFooter": "",
    "customShowTransport": True, "customShowTaxTable": True,
    "businessLogo": "", "businessName": "", "businessLegalName": "",
    "businessGstin": "", "businessPhone": "", "businessEmail": "",
    "businessAddress": "", "businessState": "", "businessStateCode": "",
    "businessWebsite": "", "businessPan": "",
    "bankName": "", "bankAccountName": "", "bankAccountNumber": "",
    "bankIfsc": "", "bankUpi": "", "authorizedSignatory": "",
    "termsText": "", "showBusinessLogo": True, "showBankDetails": True,
    "showGstin": True, "showPan": True, "showWebsite": True,
    "showTerms": True, "showSignature": True,
}
CHOICES = ("modern", "classic", "minimal", "custom")
ACCENTS = ("teal", "blue", "graphite")
BOOLEAN_KEYS = {
    "showOriginal", "showSellerMark", "compactTable", "customShowTransport",
    "customShowTaxTable", "showBusinessLogo", "showBankDetails", "showTerms",
    "showSignature", "showGstin", "showPan", "showWebsite",
}
TEXT_LENGTHS = {k: (2000 if k in {"termsText", "businessAddress", "customFooter"} else 255)
                for k in STYLE_DEFAULTS if k not in BOOLEAN_KEYS}
TEXT_LENGTHS["businessLogo"] = 360000


def validated_settings(payload):
    if not isinstance(payload, dict):
        raise serializers.ValidationError({"message": "Template settings must be an object."})
    unknown = set(payload) - set(STYLE_DEFAULTS)
    if unknown:
        raise serializers.ValidationError({"message": "Unsupported template keys: " + ", ".join(sorted(unknown))})
    result = {}
    for key, value in payload.items():
        if key in BOOLEAN_KEYS:
            if not isinstance(value, bool):
                raise serializers.ValidationError({key: ["Must be true or false."]})
        elif not isinstance(value, str) or len(value) > TEXT_LENGTHS[key]:
            raise serializers.ValidationError({key: ["Invalid or oversized text value."]})
        elif key in {"invoiceTemplate", "estimateTemplate"} and value not in CHOICES:
            raise serializers.ValidationError({key: ["Unknown document template."]})
        elif key == "accent" and value not in ACCENTS:
            raise serializers.ValidationError({key: ["Unknown accent color."]})
        elif key == "businessLogo" and value:
            # Do not allow SVG/remote URLs or unbounded image payloads.
            valid = value.startswith((
                "data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,"
            ))
            if not valid:
                raise serializers.ValidationError({key: ["Use an uploaded PNG, JPG, or WEBP image."]})
            try:
                raw = base64.b64decode(value.split(",", 1)[1], validate=True)
            except (ValueError, binascii.Error):
                raise serializers.ValidationError({key: ["Invalid image payload."]})
            if len(raw) > 250 * 1024 or len(raw) < 8:
                raise serializers.ValidationError({key: ["Logo must be at most 250 KB."]})
            try:
                with Image.open(BytesIO(raw)) as image:
                    if image.format not in {"PNG", "JPEG", "WEBP"}:
                        raise serializers.ValidationError({key: ["Image could not be processed."]})
                    if image.width > 2048 or image.height > 2048 or image.width < 1 or image.height < 1:
                        raise serializers.ValidationError({key: ["Logo dimensions must be at most 2048 × 2048."]})
                    image.verify()
            except (UnidentifiedImageError, OSError, ValueError):
                raise serializers.ValidationError({key: ["Unable to read the uploaded image."]})
        result[key] = value
    return result


def company_logo_for_template(company):
    """Small, verified preview of the private company logo for PDF/templates.

    Never expose the media URL or read arbitrarily large uploaded files.
    The raw original remains in Company.logo_image and is not copied into a
    second storage record just because someone opens the Templates page.
    """
    field = getattr(company, "logo_image", None)
    if not field:
        return ""
    try:
        with field.open("rb") as source:
            raw = source.read(10 * 1024 * 1024 + 1)
        if len(raw) > 10 * 1024 * 1024 or not raw:
            return ""
        with Image.open(BytesIO(raw)) as original:
            if original.width * original.height > 16_000_000:
                return ""
            if original.format not in {"PNG", "JPEG", "WEBP"}:
                return ""
            image = ImageOps.exif_transpose(original)
            image.thumbnail((420, 420))
            image = image.convert("RGB")
            out = BytesIO()
            image.save(out, format="JPEG", quality=75, optimize=True)
        data = out.getvalue()
        if len(data) > 250 * 1024:
            return ""
        return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
    except (FileNotFoundError, OSError, ValueError, UnidentifiedImageError,
            Image.DecompressionBombError):
        # A missing/invalid logo cannot block invoice creation.
        return ""


def company_template_defaults(company):
    """Live source data from the workshop master records, not a second form.

    Values here are inherited only when no deliberate template override was
    stored. Bank details come only from the bank selected in onboarding,
    scoped to this company; never guess a bank belonging to another tenant.
    """
    fields = {
        "businessName": company.name or "",
        "businessLegalName": company.legal_name or "",
        "businessGstin": company.gstin or "",
        "businessPhone": company.phone or "",
        "businessEmail": company.email or "",
        "businessAddress": company.address or "",
        "businessState": company.state or "",
        "businessStateCode": (company.gstin or "")[:2]
            if len(company.gstin or "") == 15 else "",
        "businessPan": (company.gstin or "")[2:12]
            if len(company.gstin or "") == 15 else "",
        "businessLogo": company_logo_for_template(company),
    }
    state = CompanySetting.objects.filter(
        company=company, category="onboarding",
    ).values_list("data", flat=True).first() or {}
    bank_id = state.get("bankAccountId")
    if bank_id:
        # Some legacy data may contain a stale/corrupt ID. Never use an
        # unscoped account lookup or crash the invoice PDF for that.
        from uuid import UUID
        try:
            bank_pk = UUID(str(bank_id))
        except (ValueError, TypeError, AttributeError):
            bank_pk = None
        bank = WorkshopFinancialAccount.objects.filter(
            company=company, kind="Bank", pk=bank_pk,
        ).first() if bank_pk else None
        if bank:
            fields.update({
                "bankName": bank.bank_name or bank.name,
                "bankAccountName": company.legal_name or company.name or "",
                "bankAccountNumber": bank.account_number or "",
                "bankIfsc": bank.ifsc or "",
            })
    return fields


def get_template_settings(company, *, sync_profile=False):
    item = CompanySetting.objects.filter(company=company, category="document_templates").first()
    saved = item.data if item and isinstance(item.data, dict) else {}
    # Historical unexpected DB fields must not leak into new PDFs.
    inherited = company_template_defaults(company)
    safe_saved = {
        k: v for k, v in saved.items()
        if k in STYLE_DEFAULTS and not (sync_profile and k in inherited)
    }
    # Saved custom branding wins, but blank or never-set fields follow updates
    # to the canonical workshop profile, uploaded logo and selected bank.
    merged = {**STYLE_DEFAULTS, **inherited, **safe_saved}
    for key, value in inherited.items():
        if not merged.get(key):
            merged[key] = value
    return merged


def template_for_document(company, kind, *, override=None):
    result = dict(override) if isinstance(override, dict) and override else get_template_settings(company)
    # Immutable selection is on the document. Never read a tenant's changed
    # settings after finalization if a snapshot exists.
    template_id = result.get("estimateTemplate" if kind == "estimate" else "invoiceTemplate")
    result["selectedTemplate"] = template_id if template_id in CHOICES else "modern"
    return result


def effective_template_for_invoice(invoice):
    """Current design for drafts, frozen design for issued documents.

    Pre-migration finalized documents did not snapshot appearance: retain
    their legacy Modern PDF instead of applying future company changes.
    """
    if invoice.template_snapshot:
        return template_for_document(invoice.company, invoice.kind, override=invoice.template_snapshot)
    if invoice.finalized_at:
        # The original ReportLab layout used the blue palette.
        return template_for_document(invoice.company, invoice.kind, override={**STYLE_DEFAULTS, "accent": "blue"})
    return template_for_document(invoice.company, invoice.kind)
