from django.urls import path
from .views import ReportsView
from .workspace import ReportsWorkspaceView
urlpatterns=[
    path("", ReportsView.as_view(), name="reports"),
    path("workspace", ReportsWorkspaceView.as_view(), name="reports-workspace"),
]
