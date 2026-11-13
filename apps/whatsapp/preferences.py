"""Workshop-scoped manual click-to-chat preference.

Default ON preserves existing workshop links. The setting is independent from
legacy Meta Cloud API fields and does not enable automatic messages.
"""
from apps.saas.models import CompanySetting

CATEGORY = "whatsapp_manual"


def manual_sharing_enabled(company):
    if company is None:
        return False
    stored = CompanySetting.objects.filter(
        company=company, category=CATEGORY,
    ).values_list("data", flat=True).first()
    # Fail closed for an explicitly disabled workshop. Missing/legacy records
    # retain manual sharing by default, with no Meta provider required.
    return not (isinstance(stored, dict) and stored.get("enabled") is False)


def save_manual_sharing_enabled(company, enabled, *, branch=None):
    if type(enabled) is not bool:
        raise ValueError("manualEnabled must be a boolean.")
    obj, _ = CompanySetting.objects.get_or_create(
        company=company,
        category=CATEGORY,
        defaults={"branch": branch, "data": {"enabled": enabled}},
    )
    if obj.data != {"enabled": enabled}:
        obj.data = {"enabled": enabled}
        obj.save(update_fields=["data", "updated_at"])
    return enabled
