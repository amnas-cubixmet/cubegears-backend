from rest_framework.routers import DefaultRouter
from .views import CustomerViewSet, CustomerReminderViewSet
router=DefaultRouter(trailing_slash=False)
router.register("",CustomerViewSet,basename="customers")
router.register("reminders",CustomerReminderViewSet,basename="customer-reminders")
urlpatterns=router.urls
