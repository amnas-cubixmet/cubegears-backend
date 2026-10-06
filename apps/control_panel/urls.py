from django.urls import path
from . import views

app_name = "panel"

urlpatterns = [
    path("login/", views.panel_login, name="login"),
    path("logout/", views.panel_logout, name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("customers/", views.customers, name="customers"),
    path("jobs/", views.jobs, name="jobs"),
    path("stock/", views.stock, name="stock"),
    path("invoices/", views.invoices, name="invoices"),
    path("staff/", views.staff, name="staff"),
]
