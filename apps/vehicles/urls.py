from rest_framework.routers import DefaultRouter
from .views import VehicleViewSet
router=DefaultRouter(trailing_slash=False); router.register("",VehicleViewSet,basename="vehicles")
urlpatterns=router.urls
