from django.urls import path
from django.views.generic.base import RedirectView
from . import views
from . import auth_views
from apps.credits import panel_views as credits_panel
from apps.credits.cycle_panel import cycle_policy_control
from apps.credits.calendar_panel import calendar_policy_control
from apps.credits.pricing_views import module_pricing

app_name = "panel"

urlpatterns = [
    path("login/", views.panel_login, name="login"),
    path("login/forgot/", auth_views.panel_forgot_password, name="forgot"),
    path("login/magic/", auth_views.panel_magic_request, name="magic-request"),
    path("login/magic/verify/<str:token>/", auth_views.panel_magic_verify, name="magic-verify"),
    path("login/password-reset/<str:uid>/<str:token>/", auth_views.panel_password_action, name="password-reset"),
    path("login/password-setup/<str:uid>/<str:token>/",
         lambda request, uid, token: auth_views.panel_password_action(request, uid, token, setup=True),
         name="password-setup"),
    path("logout/", views.panel_logout, name="logout"),
    path("audit-logs/", views.security, name="audit-logs"),
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
    path("users/<uuid:pk>/invite/", views.user_invite, name="user-invite"),

    path("plans/", views.plan_catalog, name="plans"),
    path("plans/add/", views.plan_create, name="plan-create"),
    path("plans/<int:pk>/edit/", views.plan_edit, name="plan-edit"),
    path("features/", views.feature_catalog, name="features"),
    path("features/pricing/", module_pricing, name="feature-pricing"),
    path("features/sync/", views.feature_sync, name="features-edit"),
    path("features/bundles/add/", views.feature_bundle_create, name="featurebundle-create"),
    path("features/bundles/<int:pk>/edit/", views.feature_bundle_edit, name="featurebundle-edit"),
    path("features/add/", views.feature_create, name="feature-create"),
    path("features/<int:pk>/edit/", views.feature_edit, name="feature-edit"),
    path("features/groups/add/", views.feature_group_create, name="featuregroup-create"),
    path("features/groups/<int:pk>/edit/", views.feature_group_edit, name="featuregroup-edit"),
    path("requests/", views.plan_requests, name="requests"),
    path("credits/", credits_panel.credits_control, name="credits"),
    path("credits/cycle/", cycle_policy_control, name="credit-cycle"),
    path("credits/calendar/", calendar_policy_control, name="calendar-credit-billing"),
    path("subscriptions/", views.subscriptions, name="subscriptions"),
    path("subscriptions/payments/", views.subscription_payments, name="subscription-payments"),
    path("payment-settings/", RedirectView.as_view(pattern_name="panel:credits", permanent=False), name="payment-settings"),
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
    path("staff/<uuid:pk>/invite/", views.staff_invite, name="staff-invite"),
    path("staff/<uuid:pk>/delete/", views.staff_delete, name="staff-delete"),
]

# Dedicated SaaS team role editor: scope is server-selected, never POST-selected.
urlpatterns.append(path(
    "roles/add/saas/", views.resource_action,
    {"resource": "roles", "action": "create", "role_scope": "platform"},
    name="role-saas-create",
))

for resource, (_, _, singular, _) in views.PANEL_RESOURCES.items():
    urlpatterns.append(path(f'{resource}/', views.resource_list, {'resource': resource}, name=resource))
    urlpatterns.append(path(f'{resource}/add/', views.resource_action,
                            {'resource': resource, 'action': 'create'}, name=f'{singular}-create'))
    for action, suffix in [('detail', ''), ('edit', 'edit/'), ('delete', 'delete/')]:
        urlpatterns.append(path(f'{resource}/<uuid:pk>/{suffix}', views.resource_action,
                                {'resource': resource, 'action': action}, name=f'{singular}-{action}'))
