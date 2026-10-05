from rest_framework.routers import DefaultRouter
from .views import ServiceViewSet,ServiceCategoryViewSet
router=DefaultRouter(trailing_slash=False)
router.register("categories",ServiceCategoryViewSet,basename="service-categories")
router.register("",ServiceViewSet,basename="services")
urlpatterns=router.urls
