from rest_framework.routers import DefaultRouter
from .views import EWayBillViewSet
router=DefaultRouter(trailing_slash=False)
router.register("",EWayBillViewSet,basename="eway-bills-top")
urlpatterns=router.urls
