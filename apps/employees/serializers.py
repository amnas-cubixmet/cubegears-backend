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
    teamName=serializers.CharField(source="team.name",read_only=True)
    shiftName=serializers.CharField(source="shift.name",read_only=True)
    skills=SkillSerializer(many=True,read_only=True)
    class Meta: model=Employee; exclude=("company","branch")
