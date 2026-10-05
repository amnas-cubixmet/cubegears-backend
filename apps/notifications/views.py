from django.utils import timezone
from rest_framework import decorators,response
from common.viewsets import CompanyScopedModelViewSet
from .models import Notification
from .serializers import NotificationSerializer
class NotificationViewSet(CompanyScopedModelViewSet):
    queryset=Notification.objects.all(); serializer_class=NotificationSerializer
    def get_queryset(self):
        qs=super().get_queryset()
        if self.request.user.is_superuser: return qs
        return qs.filter(user__in=[self.request.user,None])
    @decorators.action(detail=True,methods=["patch"],url_path="read")
    def mark_read(self,request,pk=None):
        obj=self.get_object(); obj.is_read=True; obj.read_at=timezone.now(); obj.save(update_fields=["is_read","read_at","updated_at"])
        return response.Response(self.get_serializer(obj).data)
