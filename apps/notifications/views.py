"""Authenticated, tenant-isolated workshop notification inbox."""
from django.db.models import Q
from django.utils import timezone
from rest_framework import decorators, response
from rest_framework.exceptions import ValidationError

from common.viewsets import CompanyScopedModelViewSet
from .estimate_responses import ESTIMATE_TYPES
from .models import Notification
from .serializers import NotificationSerializer


class NotificationViewSet(CompanyScopedModelViewSet):
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    action_permission_map = {"mark_read": "notifications.view"}

    def get_queryset(self):
        # Even a tenant superuser must not edit another staff member's private
        # notification. Preserve existing user=NULL workshop-wide notices.
        qs = super().get_queryset()
        return qs.filter(Q(user=self.request.user) | Q(user__isnull=True))

    def create(self, request, *args, **kwargs):
        if request.data.get("notification_type") in ESTIMATE_TYPES:
            raise ValidationError({
                "notification_type": "Customer estimate responses are created only by the customer portal."
            })
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        submitted_type = request.data.get("notification_type")
        if submitted_type in ESTIMATE_TYPES:
            raise ValidationError({
                "notification_type": "Customer estimate notifications cannot be forged or changed."
            })
        if obj.notification_type in ESTIMATE_TYPES:
            allowed = {"is_read", "read_at"}
            if set(request.data.keys()) - allowed:
                raise ValidationError({
                    "notification": "System-generated customer responses are read-only except read state."
                })
            if "read_at" in request.data and request.data["read_at"] is not None:
                raise ValidationError({"read_at": "Read timestamps are assigned by the server."})
            if "is_read" in request.data and request.data["is_read"] is not False:
                raise ValidationError({
                    "is_read": "Use the /read action to mark a notification read."
                })
        return super().update(request, *args, **kwargs)

    @decorators.action(detail=True, methods=["patch"], url_path="read")
    def mark_read(self, request, pk=None):
        obj = self.get_object()
        if not obj.is_read:
            obj.is_read = True
            obj.read_at = timezone.now()
            obj.save(update_fields=["is_read", "read_at", "updated_at"])
        return response.Response(self.get_serializer(obj).data)
