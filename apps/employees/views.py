import re
from urllib.parse import quote

from django.conf import settings
from django.core.mail import send_mail
from rest_framework import decorators, response
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from common.viewsets import CompanyScopedModelViewSet
from .models import (
    Team,
    Shift,
    Employee,
    EmployeeDocument,
    EmployeeActivity,
)
from .serializers import (
    TeamSerializer,
    ShiftSerializer,
    EmployeeSerializer,
    EmployeeDocumentSerializer,
    EmployeeActivitySerializer,
)


def _employee_display_no(employee):
    digits="".join(ch for ch in str(employee.employee_code or "") if ch.isdigit())
    return f"Employee {int(digits)}" if digits else "Employee"


def _shift_label(shift):
    if not shift:
        return ""
    start=shift.start_time.strftime("%I:%M %p")
    end=shift.end_time.strftime("%I:%M %p")
    return f"{shift.name} ({start} - {end})"


def _next_employee_code(company):
    highest=0
    for code in Employee.objects.filter(company=company).values_list("employee_code",flat=True):
        match=re.search(r"(\d+)$",str(code or ""))
        if match:
            highest=max(highest,int(match.group(1)))
    return f"EMP-{highest+1:04d}"


def _record_activity(employee, action, details="", actor=None, metadata=None):
    return EmployeeActivity.objects.create(
        company=employee.company,
        branch=employee.branch,
        employee=employee,
        action=action,
        details=details or "",
        actor=actor,
        metadata=metadata or {},
    )


class TeamViewSet(CompanyScopedModelViewSet):
    queryset=Team.objects.all()
    serializer_class=TeamSerializer
    permission_prefix="staff"

    def perform_create(self,serializer):
        branch=serializer.validated_data.get("branch",getattr(self.request.user,"branch",None))
        serializer.save(company=self.request.user.company,branch=branch)


class ShiftViewSet(CompanyScopedModelViewSet):
    queryset=Shift.objects.all()
    serializer_class=ShiftSerializer
    permission_prefix="staff"
    action_permission_map={"assign": "staff.edit"}

    def perform_create(self,serializer):
        branch=serializer.validated_data.get("branch",getattr(self.request.user,"branch",None))
        serializer.save(company=self.request.user.company,branch=branch)

    @decorators.action(detail=True,methods=["post"],url_path="assign")
    def assign(self,request,pk=None):
        shift=self.get_object()
        staff_ids=[str(value) for value in (request.data.get("staffIds") or [])]
        selected=Employee.objects.filter(
            company=request.user.company,
            id__in=staff_ids,
        )

        removed=Employee.objects.filter(
            company=request.user.company,
            shift=shift,
        ).exclude(id__in=staff_ids)

        for employee in removed:
            old_label=employee.shift_label or _shift_label(shift)
            employee.shift=None
            employee.shift_label=""
            employee.save(update_fields=["shift","shift_label","updated_at"])
            _record_activity(
                employee,
                "Shift Removed",
                old_label,
                request.user,
                {"shiftId":str(shift.id)},
            )

        for employee in selected:
            old_label=employee.shift_label or (
                _shift_label(employee.shift) if employee.shift else "Unassigned"
            )
            employee.shift=shift
            employee.shift_label=_shift_label(shift)
            employee.save(update_fields=["shift","shift_label","updated_at"])
            _record_activity(
                employee,
                "Shift Assigned",
                f"{old_label} → {employee.shift_label}",
                request.user,
                {"shiftId":str(shift.id)},
            )

        return response.Response({
            "shift":self.get_serializer(shift).data,
            "assignedStaffIds":[str(employee.id) for employee in selected],
        })


class EmployeeViewSet(CompanyScopedModelViewSet):
    queryset=Employee.objects.select_related("team","shift","user").all()
    serializer_class=EmployeeSerializer
    permission_prefix="staff"
    action_permission_map={
        "toggle_account": "staff.edit",
        "invite": "staff.edit",
    }

    def _resolve_role(self,role_name):
        from apps.roles.models import Role
        if not role_name:
            return None
        return Role.objects.filter(
            company=self.request.user.company,
            name__iexact=role_name,
            is_active=True,
        ).first()

    def _ensure_login_user(self,employee):
        from apps.accounts.models import User

        if employee.user:
            return employee.user
        if not employee.email:
            return None

        user=User.objects.filter(email__iexact=employee.email).first()
        if user and user.company_id not in {None, employee.company_id}:
            raise ValidationError({"email":"This login email belongs to another company."})

        if not user:
            user=User.objects.create_user(
                email=employee.email,
                password=None,
                name=employee.name,
                company=employee.company,
                branch=employee.branch,
                role=self._resolve_role(employee.role_name),
                is_active=True,
            )
        employee.user=user
        employee.save(update_fields=["user","updated_at"])
        return user

    def perform_create(self,serializer):
        company=self.request.user.company
        code=serializer.validated_data.get("employee_code") or _next_employee_code(company)
        email=serializer.validated_data.get("email") or ""
        name=serializer.validated_data.get("name") or code
        role_name=serializer.validated_data.get("role_name") or ""
        branch=serializer.validated_data.get("branch",self.request.user.branch)
        team=serializer.validated_data.get("team")
        shift=serializer.validated_data.get("shift")

        from apps.accounts.models import User
        user=None
        if email:
            user=User.objects.filter(email__iexact=email).first()
            if user and user.company_id not in {None,company.id}:
                raise ValidationError({"email":"This login email belongs to another company."})
            if not user:
                user=User.objects.create_user(
                    email=email,
                    password=None,
                    name=name,
                    company=company,
                    branch=branch,
                    role=self._resolve_role(role_name),
                    is_active=True,
                )

        obj=serializer.save(
            company=company,
            branch=branch,
            employee_code=code,
            user=user,
            department_name=serializer.validated_data.get("department_name") or (team.name if team else ""),
            shift_label=serializer.validated_data.get("shift_label") or _shift_label(shift),
        )
        _record_activity(
            obj,
            "Staff Created",
            f"{_employee_display_no(obj)} profile created.",
            self.request.user,
        )

    def perform_update(self,serializer):
        instance=self.get_object()
        before={
            "role":instance.role_name,
            "team":instance.team.name if instance.team else "",
            "shift":instance.shift.name if instance.shift else "",
            "status":instance.status,
            "designation":instance.designation,
        }

        team=serializer.validated_data.get("team",instance.team)
        shift=serializer.validated_data.get("shift",instance.shift)
        extra={}
        if "department_name" not in serializer.validated_data and team:
            extra["department_name"]=team.name
        if "shift_label" not in serializer.validated_data and shift:
            extra["shift_label"]=_shift_label(shift)

        obj=serializer.save(**extra)

        if obj.user:
            fields=[]
            if obj.user.name!=obj.name:
                obj.user.name=obj.name
                fields.append("name")
            role=self._resolve_role(obj.role_name)
            if role and obj.user.role_id!=role.id:
                obj.user.role=role
                fields.append("role")
            if fields:
                obj.user.save(update_fields=fields)

        after={
            "role":obj.role_name,
            "team":obj.team.name if obj.team else "",
            "shift":obj.shift.name if obj.shift else "",
            "status":obj.status,
            "designation":obj.designation,
        }
        changes=[
            f"{key}: {before[key] or '—'} → {after[key] or '—'}"
            for key in before
            if before[key]!=after[key]
        ]
        _record_activity(
            obj,
            "Staff Profile Updated",
            "; ".join(changes) if changes else "Profile details updated.",
            self.request.user,
            {"changes":changes},
        )

    @decorators.action(
        detail=False,
        methods=["get"],
        url_path="me",
        permission_classes=[IsAuthenticated],
    )
    def me(self,request):
        obj=Employee.objects.filter(
            company=request.user.company,
            user=request.user,
        ).select_related("team","shift","user").first()
        if not obj:
            return response.Response({"message":"No employee profile linked."},status=404)
        return response.Response(self.get_serializer(obj).data)

    @decorators.action(detail=True,methods=["patch"],url_path="toggle-account")
    def toggle_account(self,request,pk=None):
        obj=self.get_object()
        obj.status="Inactive" if obj.status=="Active" else "Active"
        obj.save(update_fields=["status","updated_at"])
        if obj.user:
            obj.user.is_active=obj.status=="Active"
            obj.user.save(update_fields=["is_active"])
        _record_activity(
            obj,
            "Account Status Changed",
            obj.status,
            request.user,
        )
        return response.Response(self.get_serializer(obj).data)

    @decorators.action(detail=True,methods=["post"],url_path="invite")
    def invite(self,request,pk=None):
        from apps.accounts.models import MagicLink

        obj=self.get_object()
        user=self._ensure_login_user(obj)
        if not user or not user.email:
            return response.Response(
                {"message":"Staff login account/email is not linked."},
                status=400,
            )

        role=self._resolve_role(obj.role_name)
        if role and user.role_id!=role.id:
            user.role=role
            user.save(update_fields=["role"])

        token=MagicLink.issue(user)
        next_path=f"/staff-management/staff/{obj.id}"
        url=(
            f"{settings.FRONTEND_URL}/magic-link/verify"
            f"?token={token}&next={quote(next_path,safe='')}"
        )
        send_mail(
            "Your CubixGear staff access",
            (
                f"Hi {obj.name},\n\n"
                f"Your CubixGear staff access is ready.\n"
                f"Open this secure link to sign in and view your staff workspace:\n{url}\n\n"
                f"Employee number: {_employee_display_no(obj)}"
            ),
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=True,
        )
        _record_activity(
            obj,
            "Login Invite Sent",
            f"Invite sent to {user.email}.",
            request.user,
        )
        return response.Response({
            "message":"Login invite sent.",
            "loginStatus":"Invite Sent",
        })


class EmployeeDocumentViewSet(CompanyScopedModelViewSet):
    queryset=EmployeeDocument.objects.select_related("employee").all()
    serializer_class=EmployeeDocumentSerializer
    permission_prefix="staff"

    def perform_create(self,serializer):
        obj=serializer.save(
            company=self.request.user.company,
            branch=self.request.user.branch,
        )
        _record_activity(
            obj.employee,
            "Document Added",
            obj.title,
            self.request.user,
            {"documentId":str(obj.id)},
        )

    def perform_update(self,serializer):
        obj=serializer.save()
        _record_activity(
            obj.employee,
            "Document Updated",
            obj.title,
            self.request.user,
            {"documentId":str(obj.id)},
        )

    def perform_destroy(self,instance):
        employee=instance.employee
        title=instance.title
        document_id=str(instance.id)
        instance.delete()
        _record_activity(
            employee,
            "Document Deleted",
            title,
            self.request.user,
            {"documentId":document_id},
        )


class EmployeeActivityViewSet(CompanyScopedModelViewSet):
    queryset=EmployeeActivity.objects.select_related("employee","actor").all()
    serializer_class=EmployeeActivitySerializer
    permission_prefix="staff"
    http_method_names=["get","head","options"]

    def get_queryset(self):
        qs=super().get_queryset()
        employee_id=self.request.query_params.get("employee")
        if employee_id:
            qs=qs.filter(employee_id=employee_id)
        return qs
