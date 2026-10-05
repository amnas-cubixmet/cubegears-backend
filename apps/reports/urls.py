from django.urls import path
from .views import ReportsView
urlpatterns=[path("",ReportsView.as_view(),name="reports")]
