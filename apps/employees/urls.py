from rest_framework.routers import DefaultRouter
from .views import *
router=DefaultRouter(trailing_slash=False)
router.register("teams",TeamViewSet,basename="teams")
router.register("shifts",ShiftViewSet,basename="shifts")
router.register("skills",SkillViewSet,basename="skills")
router.register("documents",EmployeeDocumentViewSet,basename="employee-documents")
router.register("",EmployeeViewSet,basename="employees")
urlpatterns=router.urls
