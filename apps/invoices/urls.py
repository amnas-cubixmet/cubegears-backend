from rest_framework.routers import DefaultRouter
from .views import InvoiceViewSet,EWayBillViewSet
from .quick_services import QuickServiceViewSet
router=DefaultRouter(trailing_slash=False)
router.register("e-way-bills",EWayBillViewSet,basename="eway-bills")
router.register("quick-services",QuickServiceViewSet,basename="quick-services")
router.register("",InvoiceViewSet,basename="invoices")
urlpatterns=router.urls
