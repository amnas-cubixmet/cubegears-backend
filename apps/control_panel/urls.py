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
    path("companies/<uuid:pk>/saas-invoices/<str:source>/<int:invoice_id>/pdf/",
         views.company_saas_invoice_pdf, name="company-saas-invoice-pdf"),
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
    path("plans/<int:pk>/delete/", views.plan_delete, name="plan-delete"),
    # Feature catalog, groups and published labels are managed through one
    # administrator settings page; legacy editor/sync pages are retired.
    path("features/pricing/", module_pricing, name="feature-pricing"),
    path("requests/", views.plan_requests, name="requests"),
    path("credits/", credits_panel.credits_control, name="credits"),
    path("credits/cycle/", cycle_policy_control, name="credit-cycle"),
    path("credits/calendar/", calendar_policy_control, name="calendar-credit-billing"),
    path("payment-settings/", RedirectView.as_view(pattern_name="panel:credits", permanent=False), name="payment-settings"),

    path("settings/", views.storage, name="billing-settings"),
    path("storage/", views.storage, name="storage"),
    path("security/", views.security, name="security"),

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
