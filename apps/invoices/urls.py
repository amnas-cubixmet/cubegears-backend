from rest_framework.routers import DefaultRouter
from .views import InvoiceViewSet,EWayBillViewSet
router=DefaultRouter(trailing_slash=False)
router.register("e-way-bills",EWayBillViewSet,basename="eway-bills")
router.register("",InvoiceViewSet,basename="invoices")
urlpatterns=router.urls
