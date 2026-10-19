from apps.accounts.permissions import panel_permission

RESOURCES = ['dashboard', 'companies', 'users', 'branches', 'roles', 'credits',
             'storage', 'security', 'plans', 'features', 'requests', 'customers', 'vehicles', 'jobs', 'stock', 'invoices', 'staff']
SINGULAR = {'plan': 'plans', 'feature': 'features', 'featuregroup': 'features', 'featurebundle': 'features', 'company': 'companies', 'user': 'users', 'branch': 'branches', 'role': 'roles', 'role-saas': 'roles',
            'customer': 'customers', 'vehicle': 'vehicles',
            'job': 'jobs', 'invoice': 'invoices', 'stock': 'stock', 'staff': 'staff'}


def route_allowed(user, route):
    if route == 'billing-settings':
        return (panel_permission(user, 'storage', 'view')
                and panel_permission(user, 'credits', 'view'))
    if route == 'audit-logs':
        return panel_permission(user, 'security', 'view')
    if route in ('calendar-credit-billing', 'credit-cycle'):
        return panel_permission(user, 'credits', 'view')
    if route == 'feature-pricing':
        return panel_permission(user, 'features', 'view')
    if route == 'payment-settings':
        return panel_permission(user, 'credits', 'view')
    if route == 'logout':
        return bool(user.is_authenticated and user.is_active)
    if '-' in route:
        resource, action = route.rsplit('-', 1)
        resource = SINGULAR.get(resource, resource)
        action = {'detail': 'view', 'toggle': 'edit', 'invite': 'edit'}.get(action, action)
    else:
        resource, action = route, 'view'
    return resource in RESOURCES and panel_permission(user, resource, action)


def landing_route(user):
    return next((name for name in RESOURCES if route_allowed(user, name)), None)
