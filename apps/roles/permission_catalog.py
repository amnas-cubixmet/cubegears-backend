"""Permission codes exposed to administrators and frontend clients."""
CRUD_MODULES = ['customers', 'vehicles', 'services', 'jobs', 'stock', 'invoices',
                'payments', 'expenses', 'staff', 'payroll', 'notifications']
TENANT_PERMISSIONS = sorted({f'{module}.{action}' for module in CRUD_MODULES
                            for action in ['view', 'create', 'edit', 'delete']} | {
    'dashboard.view', 'reports.view', 'company.manage', 'attendance.self',
    'attendance.manage', 'billing.view', 'billing.edit', 'storage.view',
    'storage.manage', 'settings.view', 'settings.manage',
    # Legacy attendance.manage and payroll.edit remain catalogued by CRUD_MODULES
    # and explicit compatibility values; new codes always require explicit checks.
    'attendance.team.view', 'attendance.records.manage',
    'attendance.corrections.approve', 'attendance.leave.approve',
    'attendance.overtime.approve', 'attendance.shifts.manage',
    'attendance.rules.manage', 'attendance.holidays.manage',
    'payroll.rates.manage', 'payroll.wages.finalize',
    'payroll.extras.manage', 'payroll.extras.approve',
    'payroll.adjustments.manage', 'payroll.process', 'payroll.approve',
    'payroll.payments.record', 'payroll.payments.reverse',
    'payroll.payments.view',
    'payments.reverse',
})
PANEL_MODULES = ['companies', 'users', 'branches', 'roles', 'subscriptions',
                 'plans', 'features', 'requests', 'credits',
                 'customers', 'vehicles', 'jobs', 'stock', 'invoices', 'staff']
PANEL_PERMISSIONS = sorted({f'panel.{module}.{action}' for module in PANEL_MODULES
                           for action in ['view', 'create', 'edit', 'delete']} |
                          {'panel.dashboard.view', 'panel.storage.view', 'panel.storage.edit', 'panel.security.view'})
