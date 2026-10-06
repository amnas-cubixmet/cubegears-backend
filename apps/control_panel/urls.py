from django.urls import path
from . import views

app_name = "panel"

urlpatterns = [
    path("login/", views.panel_login, name="login"),
    path("logout/", views.panel_logout, name="logout"),
    path("", views.dashboard, name="dashboard"),

    path("companies/", views.companies, name="companies"),
    path("companies/add/", views.company_create, name="company-create"),
    path("companies/<uuid:pk>/", views.company_detail, name="company-detail"),
    path("companies/<uuid:pk>/edit/", views.company_edit, name="company-edit"),
    path("companies/<uuid:pk>/delete/", views.company_delete, name="company-delete"),
    path("companies/<uuid:pk>/toggle/", views.company_toggle, name="company-toggle"),

    path("users/", views.users, name="users"),
    path("users/add/", views.user_create, name="user-create"),
    path("users/<uuid:pk>/", views.user_detail, name="user-detail"),
    path("users/<uuid:pk>/edit/", views.user_edit, name="user-edit"),
    path("users/<uuid:pk>/delete/", views.user_delete, name="user-delete"),
    path("users/<uuid:pk>/toggle/", views.user_toggle, name="user-toggle"),

    path("subscriptions/", views.subscriptions, name="subscriptions"),
    path("subscriptions/add/", views.subscription_create, name="subscription-create"),
    path("subscriptions/<uuid:pk>/", views.subscription_detail, name="subscription-detail"),
    path("subscriptions/<uuid:pk>/edit/", views.subscription_edit, name="subscription-edit"),
    path("subscriptions/<uuid:pk>/delete/", views.subscription_delete, name="subscription-delete"),

    path("storage/", views.storage, name="storage"),
    path("security/", views.security, name="security"),

    path("customers/", views.customers, name="customers"),
    path("customers/add/", views.customer_create, name="customer-create"),
    path("customers/<uuid:pk>/", views.customer_detail, name="customer-detail"),
    path("customers/<uuid:pk>/edit/", views.customer_edit, name="customer-edit"),
    path("customers/<uuid:pk>/delete/", views.customer_delete, name="customer-delete"),

    path("jobs/", views.jobs, name="jobs"),
    path("jobs/add/", views.job_create, name="job-create"),
    path("jobs/<uuid:pk>/", views.job_detail, name="job-detail"),
    path("jobs/<uuid:pk>/edit/", views.job_edit, name="job-edit"),
    path("jobs/<uuid:pk>/delete/", views.job_delete, name="job-delete"),

    path("stock/", views.stock, name="stock"),
    path("stock/add/", views.stock_create, name="stock-create"),
    path("stock/<uuid:pk>/", views.stock_detail, name="stock-detail"),
    path("stock/<uuid:pk>/edit/", views.stock_edit, name="stock-edit"),
    path("stock/<uuid:pk>/delete/", views.stock_delete, name="stock-delete"),

    path("invoices/", views.invoices, name="invoices"),
    path("invoices/add/", views.invoice_create, name="invoice-create"),
    path("invoices/<uuid:pk>/", views.invoice_detail, name="invoice-detail"),
    path("invoices/<uuid:pk>/edit/", views.invoice_edit, name="invoice-edit"),
    path("invoices/<uuid:pk>/delete/", views.invoice_delete, name="invoice-delete"),

    path("staff/", views.staff, name="staff"),
    path("staff/add/", views.staff_create, name="staff-create"),
    path("staff/<uuid:pk>/", views.staff_detail, name="staff-detail"),
    path("staff/<uuid:pk>/edit/", views.staff_edit, name="staff-edit"),
    path("staff/<uuid:pk>/delete/", views.staff_delete, name="staff-delete"),
]
