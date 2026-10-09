from rest_framework.routers import DefaultRouter

from .outside_labour_api import OutsideLabourViewSet

router = DefaultRouter(trailing_slash=False)
router.register("", OutsideLabourViewSet, basename="outside-labour")
urlpatterns = router.urls
