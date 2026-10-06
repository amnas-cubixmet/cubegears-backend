from django.urls import path
from . import views

app_name = "panel"

urlpatterns = [
    path("login/", views.panel_login, name="login"),
    path("logout/", views.panel_logout, name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("companies/", views.companies, name="companies"),
    path("companies/<uuid:pk>/toggle/", views.company_toggle, name="company-toggle"),
    path("users/", views.users, name="users"),
    path("users/<uuid:pk>/toggle/", views.user_toggle, name="user-toggle"),
    path("subscriptions/", views.subscriptions, name="subscriptions"),
    path("storage/", views.storage, name="storage"),
    path("security/", views.security, name="security"),
    path("customers/", views.customers, name="customers"),
    path("jobs/", views.jobs, name="jobs"),
    path("stock/", views.stock, name="stock"),
    path("invoices/", views.invoices, name="invoices"),
    path("staff/", views.staff, name="staff"),
]
