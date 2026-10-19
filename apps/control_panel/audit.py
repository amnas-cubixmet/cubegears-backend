"""Audit trail for platform control-panel mutations and successful sessions.

Events intentionally store only non-secret metadata. Read via platform RBAC.
"""
from apps.saas.models import SecurityEvent


def record_panel_event(request, event, metadata=None):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None
    return SecurityEvent.objects.create(
        company=getattr(user, "company", None),
        user=user,
        event=event[:100],
        ip_address=request.META.get("REMOTE_ADDR") or None,
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:512],
        metadata=metadata or {},
    )
