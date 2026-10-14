"""Permission codes exposed to administrators and frontend clients."""
CRUD_MODULES = ['customers', 'vehicles', 'services', 'jobs', 'stock', 'invoices',
                'payments', 'expenses', 'staff', 'payroll', 'notifications']
TENANT_PERMISSIONS = sorted({f'{module}.{action}' for module in CRUD_MODULES
                            for action in ['view', 'create', 'edit', 'delete']} | {
    'dashboard.view', 'reports.view', 'company.manage', 'attendance.self',
    'attendance.manage', 'billing.view', 'billing.edit', 'storage.view',
    'storage.manage', 'settings.view', 'settings.manage', 'payroll.view', 'payroll.edit',
})
PANEL_MODULES = ['companies', 'users', 'branches', 'roles', 'subscriptions',
                 'plans', 'features', 'requests', 'credits',
                 'customers', 'vehicles', 'jobs', 'stock', 'invoices', 'staff']
PANEL_PERMISSIONS = sorted({f'panel.{module}.{action}' for module in PANEL_MODULES
                           for action in ['view', 'create', 'edit', 'delete']} |
                          {'panel.dashboard.view', 'panel.storage.view', 'panel.security.view'})
