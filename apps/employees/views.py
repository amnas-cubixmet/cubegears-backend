import re

from django.db import transaction

from apps.accounts.permissions import can_delegate
from rest_framework import decorators, response
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from apps.accounts.permissions import user_has_permission

from common.viewsets import CompanyScopedModelViewSet
from apps.saas.access import enforce_user_limit
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
    queryset=Employee.objects.select_related("team","shift","user__role").all()
    serializer_class=EmployeeSerializer
    permission_prefix="staff"
    action_permission_map={
        "toggle_account": "staff.edit",
        "login_access": "staff.edit",
        "invite": "staff.edit",
    }

    def get_object(self):
        obj = super().get_object()
        user = obj.user
        if self.request.method not in {"GET", "HEAD", "OPTIONS"} and user:
            if not self.request.user.is_superuser and (user.company_id != obj.company_id or user.is_staff or user.is_superuser or (user.role and not can_delegate(self.request.user, user.role.permissions))):
                raise ValidationError("You cannot manage an account with greater permissions.")
            if self.action == "toggle_account" and user.pk == self.request.user.pk:
                raise ValidationError("You cannot disable your own account.")
        return obj

    def _resolve_role(self,role_name,role_id=None):
        from apps.roles.models import Role
        if not role_name and not role_id:
            return None
        roles = Role.objects.filter(
            company=self.request.user.company,
            is_active=True,
            is_system=False,
        )
        if role_id:
            role = roles.filter(pk=role_id).first()
        else:
            matches = roles.filter(name__iexact=role_name)
            if matches.count() > 1:
                raise ValidationError({"roleId": "Select the role by ID."})
            role = matches.first()
        if not role:
            raise ValidationError({"role": "Select an active, non-admin staff role from this company."})
        from apps.roles.assignment import assignable_role
        if not assignable_role(role, self.request.user):
            raise ValidationError({"role": "Choose a company-created staff role without owner or platform authority."})
        return role

    def _issue_staff_invitation(self, employee, user):
        from apps.accounts.staff_setup import issue_invitation, invitation_url
        from apps.whatsapp.staff_invites import queue_staff_whatsapp_invitation

        if not user or not user.is_active or not user.company_id or user.company_id != employee.company_id:
            raise ValidationError({"account": "Active, company-specific staff login required."})
        if not user.role_id or user.role.is_system or not user.role.is_active:
            raise ValidationError({"role": "A valid non-admin staff login role is required."})
        record = issue_invitation(user, issuer=self.request.user)
        delivery = queue_staff_whatsapp_invitation(record, employee)
        employee._setup_link = invitation_url(record)
        employee._whatsapp_invite_status = delivery
        _record_activity(
            employee, "Staff Password Invitation Created",
            "A one-use 24-hour password link was created. Delivery: " + delivery,
            self.request.user,
        )
        return record

    def _ensure_login_user(self, employee):
        from apps.accounts.models import User
        from apps.accounts.phone import normalize_phone

        if employee.user:
            return employee.user
        if not user_has_permission(self.request.user, "company.manage"):
            raise PermissionDenied("Only a company admin can create staff login accounts.")
        try:
            mobile = normalize_phone(employee.phone)
        except Exception:
            raise ValidationError({"phone": "Add an international mobile number before enabling login."})
        role = self._resolve_role(employee.role_name)
        if not role:
            raise ValidationError({"role": "Choose a role before inviting this staff member."})
        if employee.email and User.objects.filter(email__iexact=employee.email.strip()).exists():
            raise ValidationError({"email": "This email is already linked to a login account."})
        if User.objects.filter(company=employee.company, normalized_phone=mobile).exists():
            raise ValidationError({"phone": "This mobile already has a login in this workshop."})
        if employee.status == "Active":
            enforce_user_limit(employee.company)
        user = User.objects.create_user(
            email=employee.email.strip().lower() or None,
            password=None, name=employee.name, phone=mobile,
            company=employee.company, branch=employee.branch, role=role,
            is_active=employee.status == "Active", is_staff=False, is_superuser=False,
        )
        employee.user = user
        employee.save(update_fields=["user", "updated_at"])
        return user

    @transaction.atomic
    def perform_create(self, serializer):
        from apps.accounts.models import User
        from apps.accounts.phone import normalize_phone

        company = self.request.user.company
        code = serializer.validated_data.get("employee_code") or _next_employee_code(company)
        email = (serializer.validated_data.get("email") or "").strip().lower()
        name = serializer.validated_data.get("name") or code
        role_name = serializer.validated_data.get("role_name") or ""
        role_id = serializer.validated_data.pop("roleId", None)
        login_enabled = serializer.validated_data.pop("loginAccessEnabled", False)
        requested_role = self._resolve_role(role_name, role_id) if role_name or role_id else None
        branch = serializer.validated_data.get("branch", self.request.user.branch)
        team = serializer.validated_data.get("team")
        shift = serializer.validated_data.get("shift")
        status_value = serializer.validated_data.get("status", "Active")
        mobile = normalize_phone(serializer.validated_data.get("phone"))
        serializer.validated_data["phone"] = mobile

        user = None
        if login_enabled:
            if not user_has_permission(self.request.user, "company.manage"):
                raise PermissionDenied("Only a company admin can create staff login accounts.")
            if requested_role is None:
                raise ValidationError({"role": "Choose an assignable staff role before enabling login."})
            if email and User.objects.filter(email__iexact=email).exists():
                raise ValidationError({"email": "This email is already linked to a login account."})
            if User.objects.filter(company=company, normalized_phone=mobile).exists():
                raise ValidationError({"phone": "This mobile already has a login account in this workshop."})
            if status_value == "Active":
                enforce_user_limit(company)
            user = User.objects.create_user(
                email=email or None, password=None, name=name,
                phone=mobile, company=company, branch=branch,
                role=requested_role, is_active=status_value in {"Active", "Probation", "Notice Period"},
                is_staff=False, is_superuser=False, email_verified=False,
            )

        obj = serializer.save(
            company=company, branch=branch, employee_code=code, email=email,
            payment_type="daily", user=user,
            department_name=serializer.validated_data.get("department_name") or (team.name if team else ""),
            shift_label=serializer.validated_data.get("shift_label") or _shift_label(shift),
        )
        _record_activity(obj, "Staff Created", f"{_employee_display_no(obj)} profile created.", self.request.user)
        if user and user.is_active:
            self._issue_staff_invitation(obj, user)

    @transaction.atomic
    def perform_update(self,serializer):
        instance=self.get_object()
        if instance.user and instance.user.role and instance.user.role.is_system:
            raise PermissionDenied("Protected company owner accounts cannot be edited here.")
        selected_id = serializer.validated_data.pop("roleId", None)
        requested_name=serializer.validated_data.get("role_name")
        requested_email=serializer.validated_data.get("email")
        if instance.user and requested_email is not None and requested_email.strip().lower() != (instance.user.email or '').lower():
            raise ValidationError({"email": "To change a linked login email, contact your company administrator."})
        if requested_name is not None and (requested_name != instance.role_name or (selected_id and instance.user and selected_id != instance.user.role_id)):
            if not user_has_permission(self.request.user, "company.manage"):
                raise PermissionDenied("Only a company admin can change a login role.")
            if requested_name.strip().upper().replace(" ", "_") in {"SUPER_ADMIN", "PLATFORM_ADMIN"}:
                raise PermissionDenied("Protected admin roles cannot be assigned through staff editing.")
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

        # Staff employment deactivation is also a login revocation. Enabling
        # employment later must not silently re-enable a disabled login.
        if obj.user and obj.status not in {"Active", "Probation", "Notice Period"}:
            from django.utils import timezone
            from apps.accounts.models import StaffPasswordInvitation
            if obj.user.is_active:
                obj.user.is_active = False
                obj.user.save(update_fields=["is_active", "updated_at"])
            StaffPasswordInvitation.objects.filter(
                user=obj.user, used_at__isnull=True, invalidated_at__isnull=True,
            ).update(invalidated_at=timezone.now(), delivery_status="superseded")

        # Preserve legacy monthly/commission values only in historical payroll
        # tables; active staff are enrolled in daily wages only.
        if obj.payment_type != "daily":
            obj.payment_type = "daily"
            obj.save(update_fields=["payment_type", "updated_at"])

        if obj.user:
            fields=[]
            if obj.user.name!=obj.name:
                obj.user.name=obj.name
                fields.append("name")
            if "role_name" in serializer.validated_data:
                role=self._resolve_role(obj.role_name, selected_id)
                if obj.user.role_id != (role.pk if role else None):
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
        if obj.user_id == request.user.id or (obj.user and obj.user.role and obj.user.role.is_system):
            raise PermissionDenied("Company owner and current user accounts cannot be disabled here.")
        if obj.status != "Active" and obj.user and not obj.user.is_active:
            enforce_user_limit(request.user.company)
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

    @decorators.action(detail=True, methods=["patch"], url_path="login-access")
    def login_access(self, request, pk=None):
        if not user_has_permission(request.user, "company.manage"):
            raise PermissionDenied("Only a company admin can change staff login access.")
        enabled = request.data.get("enabled")
        if not isinstance(enabled, bool):
            raise ValidationError({"enabled":"Provide a boolean enabled value."})
        with transaction.atomic():
            employee = self.get_object()
            if employee.user_id == request.user.pk or (
                employee.user and (employee.user.is_superuser or employee.user.is_staff or
                                   (employee.user.role and employee.user.role.is_system))
            ):
                raise PermissionDenied("Owner and platform account login cannot be changed here.")
            if not employee.user:
                if not enabled:
                    return response.Response(self.get_serializer(employee).data)
                employee.user = self._ensure_login_user(employee)
            if enabled and employee.status not in {"Active", "Probation", "Notice Period"}:
                raise ValidationError({"status":"Activate the employee before enabling login."})
            if enabled and not employee.user.is_active:
                enforce_user_limit(request.user.company)
            employee.user.is_active = enabled
            employee.user.save(update_fields=["is_active", "updated_at"])
            if not enabled:
                from apps.accounts.models import StaffPasswordInvitation
                StaffPasswordInvitation.objects.filter(
                    user=employee.user,used_at__isnull=True,
                    invalidated_at__isnull=True,
                ).update(invalidated_at=__import__("django.utils.timezone",fromlist=["now"]).now())
            _record_activity(employee, "Staff Login Access Changed",
                             "Enabled" if enabled else "Disabled", request.user)
        return response.Response(self.get_serializer(employee).data)

    @decorators.action(detail=True, methods=["post"], url_path="invite")
    def invite(self, request, pk=None):
        if not user_has_permission(request.user, "company.manage"):
            raise PermissionDenied("Only a company admin can invite staff login accounts.")
        with transaction.atomic():
            obj = self.get_object()
            if obj.status != "Active":
                raise ValidationError({"status":"Activate this employee before inviting."})
            user = self._ensure_login_user(obj)
            role = self._resolve_role(obj.role_name, user.role_id)
            if not role:
                raise ValidationError({"role":"A valid workshop staff role is required."})
            if user.role_id != role.pk:
                user.role = role
                user.save(update_fields=["role"])
            from datetime import timedelta
            from django.utils import timezone
            from apps.accounts.models import StaffPasswordInvitation
            from apps.accounts.staff_setup import invitation_url

            # Rapid double-clicks/retries return the same still-valid invite.
            # They must never create and deliver two different password links.
            recent = StaffPasswordInvitation.objects.select_for_update().filter(
                user=user, used_at__isnull=True, invalidated_at__isnull=True,
                expires_at__gt=timezone.now(),
                created_at__gte=timezone.now() - timedelta(seconds=60),
            ).order_by("-created_at").first()
            if recent:
                invite = recent
                url = invitation_url(recent)
                delivery = recent.delivery_status
            else:
                invite = self._issue_staff_invitation(obj, user)
                url = obj._setup_link
                delivery = obj._whatsapp_invite_status
        return response.Response({
            "message": "New secure 24-hour password link issued.",
            "setupLink": url, "expiresAt": invite.expires_at,
            "whatsappInviteStatus": delivery,
            "loginStatus": "Active" if user.has_usable_password() else "Pending Password Setup",
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

    @transaction.atomic
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
