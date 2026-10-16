from rest_framework import serializers
from apps.branches.models import Branch
from apps.accounts.permissions import can_delegate
from apps.roles.models import Role
from .models import Team,Shift,Employee,EmployeeDocument,EmployeeActivity

class TeamSerializer(serializers.ModelSerializer):
    leadName=serializers.CharField(source="lead.name",read_only=True)
    branchId=serializers.PrimaryKeyRelatedField(
        source="branch",
        queryset=Branch.objects.all(),
        required=False,
        allow_null=True,
    )
    branchName=serializers.CharField(source="branch.name",read_only=True)

    class Meta:
        model=Team
        exclude=("company","branch")

    def validate_branchId(self,value):
        request=self.context.get("request")
        company=getattr(getattr(request,"user",None),"company",None)
        if value and company and value.company_id!=company.id:
            raise serializers.ValidationError("Invalid branch for this company.")
        return value

class ShiftSerializer(serializers.ModelSerializer):
    assignedStaffIds=serializers.SerializerMethodField()
    branchId=serializers.PrimaryKeyRelatedField(
        source="branch",
        queryset=Branch.objects.all(),
        required=False,
        allow_null=True,
    )
    branchName=serializers.CharField(source="branch.name",read_only=True)

    class Meta:
        model=Shift
        exclude=("company","branch")

    def get_assignedStaffIds(self,obj):
        return [str(pk) for pk in obj.employees.values_list("id",flat=True)]

    def validate_branchId(self,value):
        request=self.context.get("request")
        company=getattr(getattr(request,"user",None),"company",None)
        if value and company and value.company_id!=company.id:
            raise serializers.ValidationError("Invalid branch for this company.")
        return value


class EmployeeDocumentSerializer(serializers.ModelSerializer):
    staffId=serializers.UUIDField(source="employee_id",read_only=True)
    staffName=serializers.CharField(source="employee.name",read_only=True)
    designation=serializers.CharField(source="employee.designation",read_only=True)
    name=serializers.CharField(source="title",read_only=True)
    type=serializers.CharField(source="document_type",read_only=True)
    fileUrl=serializers.URLField(source="file_url",read_only=True)
    expiryDate=serializers.DateField(source="expiry_date",read_only=True)
    uploadedDate=serializers.DateTimeField(source="created_at",read_only=True)
    status=serializers.SerializerMethodField()

    class Meta:
        model=EmployeeDocument
        exclude=("company","branch")

    def get_status(self,obj):
        if not obj.expiry_date:
            return "Valid"
        from django.utils import timezone
        days=(obj.expiry_date-timezone.localdate()).days
        if days < 0:
            return "Expired"
        if days <= 30:
            return "Expiring Soon"
        return "Valid"

class EmployeeActivitySerializer(serializers.ModelSerializer):
    actorName=serializers.CharField(source="actor.name",read_only=True)
    class Meta:
        model=EmployeeActivity
        exclude=("company","branch")

class EmployeeSerializer(serializers.ModelSerializer):
    employeeId=serializers.CharField(source="employee_code",required=False)
    displayEmployeeNo=serializers.SerializerMethodField()
    role=serializers.CharField(source="role_name",required=False,allow_blank=True)
    department=serializers.CharField(source="department_name",required=False,allow_blank=True)
    shift=serializers.CharField(source="shift_label",required=False,allow_blank=True)
    joiningDate=serializers.DateField(source="joining_date",required=False,allow_null=True)
    employmentStatus=serializers.CharField(source="status",required=False)
    emergencyContact=serializers.CharField(source="emergency_contact",required=False,allow_blank=True)
    branchName=serializers.CharField(source="branch.name",read_only=True)
    branchId=serializers.PrimaryKeyRelatedField(
        source="branch",
        queryset=Branch.objects.all(),
        required=False,
        allow_null=True,
    )
    team=TeamSerializer(read_only=True)
    teamId=serializers.PrimaryKeyRelatedField(source="team",queryset=Team.objects.all(),required=False,allow_null=True,write_only=True)
    teamName=serializers.CharField(source="team.name",read_only=True)
    shiftId=serializers.PrimaryKeyRelatedField(source="shift",queryset=Shift.objects.all(),required=False,allow_null=True,write_only=True)
    shiftDetails=ShiftSerializer(source="shift",read_only=True)
    shiftName=serializers.CharField(source="shift.name",read_only=True)
    roleId=serializers.UUIDField(write_only=True, required=False)
    assignedRoleId=serializers.SerializerMethodField()
    accountStatus=serializers.SerializerMethodField()
    loginStatus=serializers.SerializerMethodField()
    emailDeliveryStatus=serializers.SerializerMethodField()

    class Meta:
        model=Employee
        exclude=("company","branch","employee_code","role_name","department_name","shift_label","joining_date","status","emergency_contact","payment_type")
        # Never allow an employee payload to bind a different user's login.
        read_only_fields=("user",)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.user_id and instance.user.role_id:
            data['role'] = instance.user.role.name
        return data

    def get_assignedRoleId(self,obj):
        return str(obj.user.role_id) if obj.user_id and obj.user.role_id else None

    def get_emailDeliveryStatus(self,obj):
        # Only present in the response that scheduled the invitation.
        # Status is not persisted, as provider delivery is not inbox receipt.
        state = getattr(obj, "_invite_email_delivery", None)
        return state.get("status") if state else None

    def get_displayEmployeeNo(self,obj):
        digits="".join(ch for ch in str(obj.employee_code or "") if ch.isdigit())
        return f"Employee {int(digits)}" if digits else "Employee"

    def validate(self,attrs):
        request=self.context.get("request")
        company=getattr(getattr(request,"user",None),"company",None)
        if not company:
            return attrs

        forbidden = {'permissions', 'permissionOverrides', 'permission_overrides', 'customPermissions', 'user_permissions', 'user', 'company', 'company_id', 'is_staff', 'is_superuser'} & set(self.initial_data)
        if forbidden:
            raise serializers.ValidationError({key: 'Staff access comes only from an assigned company role.' for key in forbidden})
        actor = request.user
        selected_id = attrs.get('roleId')
        if selected_id:
            from apps.roles.assignment import assignable_role
            selected = Role.objects.filter(pk=selected_id, company=company).first()
            if not selected or not assignable_role(selected, actor):
                raise serializers.ValidationError({'roleId': 'Select an assignable role created in this company.'})
            if attrs.get('role_name') and attrs['role_name'].casefold() != selected.name.casefold():
                raise serializers.ValidationError({'roleId': 'Role name and selected role do not match.'})
            attrs['role_name'] = selected.name
        linked = attrs.get("user", getattr(self.instance, "user", None))
        if linked and (linked.company_id != company.id or linked.is_staff or linked.is_superuser):
            raise serializers.ValidationError({"user": "Select a workshop account in this company."})
        if linked and linked.role and not can_delegate(actor, linked.role.permissions):
            raise serializers.ValidationError({"user": "You cannot manage an account with greater permissions."})
        if linked and "role_name" in attrs and not attrs["role_name"].strip():
            raise serializers.ValidationError({"role": "An existing login must keep an assigned staff role."})
        role_name = attrs.get("role_name")
        if role_name:
            roles = Role.objects.filter(company=company, name__iexact=role_name, is_active=True, is_system=False)
            if not selected_id and roles.count() > 1:
                raise serializers.ValidationError({"roleId": "Select the company role by ID; multiple roles have this name."})
            role = roles.filter(pk=selected_id).first() if selected_id else roles.first()
            if not role:
                raise serializers.ValidationError({"role": "Select an active role belonging to this company."})
            from apps.roles.assignment import assignable_role
            if not assignable_role(role, actor):
                raise serializers.ValidationError({"role": "You cannot assign this role."})
        branch=attrs.get("branch")
        team=attrs.get("team")
        shift=attrs.get("shift")

        if branch and branch.company_id!=company.id:
            raise serializers.ValidationError({"branchId":"Invalid branch for this company."})
        if team and team.company_id!=company.id:
            raise serializers.ValidationError({"teamId":"Invalid team for this company."})
        if shift and shift.company_id!=company.id:
            raise serializers.ValidationError({"shiftId":"Invalid shift for this company."})
        return attrs

    def get_accountStatus(self,obj):
        return "Active" if (not obj.user or obj.user.is_active) and obj.status=="Active" else "Inactive"

    def get_loginStatus(self,obj):
        if not obj.user:
            return "Not Invited"
        if not obj.user.is_active:
            return "Inactive"
        return "Active" if obj.user.has_usable_password() else "Password Setup Pending"
