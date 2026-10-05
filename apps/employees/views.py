from common.viewsets import CompanyScopedModelViewSet
from .models import Team,Shift,Skill,Employee,EmployeeDocument
from .serializers import *
class TeamViewSet(CompanyScopedModelViewSet): queryset=Team.objects.all(); serializer_class=TeamSerializer
class ShiftViewSet(CompanyScopedModelViewSet): queryset=Shift.objects.all(); serializer_class=ShiftSerializer
class SkillViewSet(CompanyScopedModelViewSet): queryset=Skill.objects.all(); serializer_class=SkillSerializer
class EmployeeViewSet(CompanyScopedModelViewSet):
    queryset=Employee.objects.select_related("team","shift","user").prefetch_related("skills").all(); serializer_class=EmployeeSerializer
class EmployeeDocumentViewSet(CompanyScopedModelViewSet):
    queryset=EmployeeDocument.objects.select_related("employee").all(); serializer_class=EmployeeDocumentSerializer
