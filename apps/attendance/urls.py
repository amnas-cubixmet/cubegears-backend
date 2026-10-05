from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import *
router=DefaultRouter(trailing_slash=False)
router.register("records",AttendanceRecordViewSet,basename="attendance-records")
router.register("leave",LeaveRequestViewSet,basename="leave")
router.register("overtime",OvertimeRequestViewSet,basename="overtime")
router.register("holidays",HolidayViewSet,basename="holidays")
router.register("rules",AttendanceRuleViewSet,basename="attendance-rules")
urlpatterns=[path("toggle",ToggleAttendanceView.as_view(),name="attendance-toggle"),*router.urls]
