from rest_framework import serializers
from .models import Team,Shift,Skill,Employee,EmployeeDocument

class TeamSerializer(serializers.ModelSerializer):
    class Meta: model=Team; exclude=("company","branch")

class ShiftSerializer(serializers.ModelSerializer):
    class Meta: model=Shift; exclude=("company","branch")

class SkillSerializer(serializers.ModelSerializer):
    class Meta: model=Skill; exclude=("company","branch")

class EmployeeDocumentSerializer(serializers.ModelSerializer):
    class Meta: model=EmployeeDocument; exclude=("company","branch")

class EmployeeSerializer(serializers.ModelSerializer):
    employeeId=serializers.CharField(source="employee_code",required=False)
    role=serializers.CharField(source="role_name",required=False,allow_blank=True)
    department=serializers.CharField(source="department_name",required=False,allow_blank=True)
    shift=serializers.CharField(source="shift_label",required=False,allow_blank=True)
    joiningDate=serializers.DateField(source="joining_date",required=False,allow_null=True)
    employmentStatus=serializers.CharField(source="status",required=False)
    emergencyContact=serializers.CharField(source="emergency_contact",required=False,allow_blank=True)
    paymentType=serializers.CharField(source="payment_type",required=False,allow_blank=True)
    teamName=serializers.CharField(source="team.name",read_only=True)
    shiftName=serializers.CharField(source="shift.name",read_only=True)
    skills=SkillSerializer(many=True,read_only=True)
    accountStatus=serializers.SerializerMethodField()
    loginStatus=serializers.SerializerMethodField()

    class Meta:
        model=Employee
        exclude=("company","branch","employee_code","role_name","department_name","shift_label","joining_date","status","emergency_contact","payment_type")
        extra_kwargs={"user":{"required":False,"allow_null":True}}

    def get_accountStatus(self,obj):
        return "Active" if (not obj.user or obj.user.is_active) and obj.status=="Active" else "Inactive"

    def get_loginStatus(self,obj):
        if not obj.user: return "Not Invited"
        return "Active" if obj.user.last_login else "Invite Ready"
