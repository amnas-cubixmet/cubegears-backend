from django.conf import settings
from django.core.mail import send_mail
from rest_framework import decorators,response
from common.viewsets import CompanyScopedModelViewSet
from .models import Team,Shift,Skill,Employee,EmployeeDocument
from .serializers import *
class TeamViewSet(CompanyScopedModelViewSet): queryset=Team.objects.all(); serializer_class=TeamSerializer
class ShiftViewSet(CompanyScopedModelViewSet): queryset=Shift.objects.all(); serializer_class=ShiftSerializer
class SkillViewSet(CompanyScopedModelViewSet): queryset=Skill.objects.all(); serializer_class=SkillSerializer
class EmployeeViewSet(CompanyScopedModelViewSet):
    queryset=Employee.objects.select_related("team","shift","user").prefetch_related("skills").all()
    serializer_class=EmployeeSerializer

    @decorators.action(detail=True,methods=["patch"],url_path="toggle-account")
    def toggle_account(self,request,pk=None):
        obj=self.get_object()
        obj.status="Inactive" if obj.status=="Active" else "Active"
        obj.save(update_fields=["status","updated_at"])
        if obj.user:
            obj.user.is_active=obj.status=="Active"
            obj.user.save(update_fields=["is_active"])
        return response.Response(self.get_serializer(obj).data)

    @decorators.action(detail=True,methods=["post"],url_path="invite")
    def invite(self,request,pk=None):
        from apps.accounts.models import MagicLink
        obj=self.get_object()
        if not obj.user or not obj.user.email:
            return response.Response({"message":"Staff login account/email is not linked."},status=400)
        token=MagicLink.issue(obj.user)
        url=f"{settings.FRONTEND_URL}/magic-link/verify?token={token}"
        send_mail("CubixGear login invite",f"Use this link to sign in: {url}",settings.DEFAULT_FROM_EMAIL,[obj.user.email],fail_silently=True)
        return response.Response({"message":"Login invite sent.","loginStatus":"Invite Sent"})
class EmployeeDocumentViewSet(CompanyScopedModelViewSet):
    queryset=EmployeeDocument.objects.select_related("employee").all(); serializer_class=EmployeeDocumentSerializer
