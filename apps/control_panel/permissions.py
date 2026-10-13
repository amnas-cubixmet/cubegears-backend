from apps.accounts.permissions import panel_permission

RESOURCES = ['dashboard', 'companies', 'users', 'branches', 'roles', 'subscriptions',
             'storage', 'security', 'customers', 'vehicles', 'jobs', 'stock', 'invoices', 'staff']
SINGULAR = {'company': 'companies', 'user': 'users', 'branch': 'branches', 'role': 'roles',
            'subscription': 'subscriptions', 'customer': 'customers', 'vehicle': 'vehicles',
            'job': 'jobs', 'invoice': 'invoices', 'stock': 'stock', 'staff': 'staff'}


def route_allowed(user, route):
    if route == 'logout':
        return bool(user.is_authenticated and user.is_active)
    if '-' in route:
        resource, action = route.rsplit('-', 1)
        resource = SINGULAR.get(resource, resource)
        action = {'detail': 'view', 'toggle': 'edit'}.get(action, action)
    else:
        resource, action = route, 'view'
    return resource in RESOURCES and panel_permission(user, resource, action)


def landing_route(user):
    return next((name for name in RESOURCES if route_allowed(user, name)), None)
