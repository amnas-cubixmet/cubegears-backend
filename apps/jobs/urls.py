from rest_framework.routers import DefaultRouter
from .views import JobViewSet
router=DefaultRouter(trailing_slash=False); router.register("",JobViewSet,basename="jobs")
urlpatterns=router.urls
