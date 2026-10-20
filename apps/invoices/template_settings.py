"""Validated tenant invoice/estimate appearance settings shared by UI and PDF.

This is styling configuration, not a way to override legal GST identity or totals.
Never trust client-requested prices or untrusted external image URLs in PDF.
"""
import base64
import binascii
from rest_framework import serializers
from apps.saas.models import CompanySetting

STYLE_DEFAULTS = {
    "invoiceTemplate": "modern", "estimateTemplate": "modern",
    "accent": "teal", "showOriginal": True, "showSellerMark": True,
    "compactTable": False, "customName": "My Custom Template",
    "customHeader": "Tax Invoice", "customFooter": "Thank you for your business.",
    "customShowTransport": True, "customShowTaxTable": True,
    "businessLogo": "", "businessName": "", "businessLegalName": "",
    "businessGstin": "", "businessPhone": "", "businessEmail": "",
    "businessAddress": "", "businessState": "", "businessStateCode": "",
    "businessWebsite": "", "businessPan": "",
    "bankName": "", "bankAccountName": "", "bankAccountNumber": "",
    "bankIfsc": "", "bankUpi": "", "authorizedSignatory": "",
    "termsText": "", "showBusinessLogo": True, "showBankDetails": True,
    "showTerms": True, "showSignature": True,
}
CHOICES = ("modern", "classic", "minimal", "custom")
ACCENTS = ("teal", "blue", "graphite")
BOOLEAN_KEYS = {
    "showOriginal", "showSellerMark", "compactTable", "customShowTransport",
    "customShowTaxTable", "showBusinessLogo", "showBankDetails", "showTerms",
    "showSignature",
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
        result[key] = value
    return result


def get_template_settings(company):
    item = CompanySetting.objects.filter(company=company, category="document_templates").first()
    saved = item.data if item and isinstance(item.data, dict) else {}
    # Historical unexpected DB fields must not leak into new PDFs.
    safe_saved = {k: v for k, v in saved.items() if k in STYLE_DEFAULTS}
    return {**STYLE_DEFAULTS, **safe_saved}


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
