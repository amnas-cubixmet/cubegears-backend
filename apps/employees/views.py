from django.conf import settings
from django.core.mail import send_mail
from rest_framework import decorators,response
from rest_framework.exceptions import ValidationError
from common.viewsets import CompanyScopedModelViewSet
from .models import Team,Shift,Skill,Employee,EmployeeDocument
from .serializers import *
class TeamViewSet(CompanyScopedModelViewSet): queryset=Team.objects.all(); serializer_class=TeamSerializer
class ShiftViewSet(CompanyScopedModelViewSet): queryset=Shift.objects.all(); serializer_class=ShiftSerializer
class SkillViewSet(CompanyScopedModelViewSet):
    queryset=Skill.objects.all()
    serializer_class=SkillSerializer
    action_permission_map={"assign": "staff.edit"}

    @decorators.action(detail=True,methods=["post"],url_path="assign")
    def assign(self,request,pk=None):
        skill=self.get_object()
        staff_ids=request.data.get("staffIds") or []
        employees=Employee.objects.filter(company=request.user.company,id__in=staff_ids)
        for employee in employees:
            employee.skills.add(skill)
        return response.Response({"skill":SkillSerializer(skill).data,"assignedStaffIds":[str(x.id) for x in employees]})
class EmployeeViewSet(CompanyScopedModelViewSet):
    queryset=Employee.objects.select_related("team","shift","user").prefetch_related("skills").all()
    serializer_class=EmployeeSerializer
    action_permission_map={
        "toggle_account": "staff.edit",
        "invite": "staff.edit",
    }

    def perform_create(self,serializer):
        from apps.accounts.models import User
        from apps.roles.models import Role
        company=self.request.user.company
        count=Employee.objects.filter(company=company).count()+1
        code=serializer.validated_data.get("employee_code") or f"EMP-{count:04d}"
        email=serializer.validated_data.get("email") or ""
        name=serializer.validated_data.get("name") or code
        user=None
        if email:
            user=User.objects.filter(email__iexact=email).first()
            if user and user.company_id not in {None, company.id}:
                raise ValidationError({"email":"This login email belongs to another company."})
            if not user:
                role_name=serializer.validated_data.get("role_name") or ""
                role=Role.objects.filter(company=company,name__iexact=role_name).first()
                user=User.objects.create_user(email=email,name=name,company=company,branch=self.request.user.branch,role=role)
        serializer.save(company=company,branch=self.request.user.branch,employee_code=code,user=user)

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
