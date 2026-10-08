from rest_framework import serializers
from .models import Team,Shift,Skill,Employee,EmployeeDocument,EmployeeActivity

class TeamSerializer(serializers.ModelSerializer):
    leadName=serializers.CharField(source="lead.name",read_only=True)
    class Meta: model=Team; exclude=("company","branch")

class ShiftSerializer(serializers.ModelSerializer):
    assignedStaffIds=serializers.SerializerMethodField()

    class Meta:
        model=Shift
        exclude=("company","branch")

    def get_assignedStaffIds(self,obj):
        return [str(pk) for pk in obj.employees.values_list("id",flat=True)]


class SkillSerializer(serializers.ModelSerializer):
    assignedStaffIds=serializers.SerializerMethodField()

    class Meta:
        model=Skill
        exclude=("company","branch")

    def get_assignedStaffIds(self,obj):
        return [str(pk) for pk in obj.employees.values_list("id",flat=True)]

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
    paymentType=serializers.CharField(source="payment_type",required=False,allow_blank=True)
    team=TeamSerializer(read_only=True)
    teamId=serializers.PrimaryKeyRelatedField(source="team",queryset=Team.objects.all(),required=False,allow_null=True,write_only=True)
    teamName=serializers.CharField(source="team.name",read_only=True)
    shiftId=serializers.PrimaryKeyRelatedField(source="shift",queryset=Shift.objects.all(),required=False,allow_null=True,write_only=True)
    shiftDetails=ShiftSerializer(source="shift",read_only=True)
    shiftName=serializers.CharField(source="shift.name",read_only=True)
    skills=SkillSerializer(many=True,read_only=True)
    skillIds=serializers.PrimaryKeyRelatedField(source="skills",queryset=Skill.objects.all(),many=True,required=False,write_only=True)
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

        team=attrs.get("team")
        shift=attrs.get("shift")
        skills=attrs.get("skills")

        if team and team.company_id!=company.id:
            raise serializers.ValidationError({"teamId":"Invalid team for this company."})
        if shift and shift.company_id!=company.id:
            raise serializers.ValidationError({"shiftId":"Invalid shift for this company."})
        if skills and any(skill.company_id!=company.id for skill in skills):
            raise serializers.ValidationError({"skillIds":"One or more skills do not belong to this company."})
        return attrs

    def get_accountStatus(self,obj):
        return "Active" if (not obj.user or obj.user.is_active) and obj.status=="Active" else "Inactive"

    def get_loginStatus(self,obj):
        if not obj.user: return "Not Invited"
        return "Active" if obj.user.last_login else "Invite Ready"
