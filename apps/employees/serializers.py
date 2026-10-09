from rest_framework import serializers
from apps.branches.models import Branch
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
    accountStatus=serializers.SerializerMethodField()
    loginStatus=serializers.SerializerMethodField()

    class Meta:
        model=Employee
        exclude=("company","branch","employee_code","role_name","department_name","shift_label","joining_date","status","emergency_contact","payment_type")
        extra_kwargs={"user":{"required":False,"allow_null":True}}

    def get_displayEmployeeNo(self,obj):
        digits="".join(ch for ch in str(obj.employee_code or "") if ch.isdigit())
        return f"Employee {int(digits)}" if digits else "Employee"

    def validate(self,attrs):
        request=self.context.get("request")
        company=getattr(getattr(request,"user",None),"company",None)
        if not company:
            return attrs

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
        if not obj.user: return "Not Invited"
        return "Active" if obj.user.last_login else "Invite Ready"
