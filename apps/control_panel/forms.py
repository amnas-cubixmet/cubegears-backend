from django import forms
from django.utils.text import slugify
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password

from apps.accounts.models import User
from apps.accounts.permissions import can_delegate
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.employees.models import Employee, Shift, Team
from apps.inventory.models import StockCategory, StockItem, Supplier
from apps.invoices.models import Invoice
from apps.jobs.models import Job
from apps.roles.models import Role
from apps.saas.models import Subscription, SubscriptionPlan, FeatureGroup, PlanFeature, PlanFeatureAccess
from apps.vehicles.models import Vehicle


class PanelLoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        "class": "form-control",
        "placeholder": "Email address",
        "autocomplete": "email",
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        "class": "form-control",
        "placeholder": "Password",
        "autocomplete": "current-password",
    }))

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.user_cache = None

    def clean(self):
        data = super().clean()
        email = data.get("email")
        password = data.get("password")
        if email and password:
            self.user_cache = authenticate(self.request, email=email.lower(), password=password)
            if not self.user_cache:
                raise forms.ValidationError("Invalid email or password.")
            if not self.user_cache.is_active:
                raise forms.ValidationError("This account is disabled.")
        return data

    def get_user(self):
        return self.user_cache


class AdminModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            current = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (current + " form-control").strip()
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("rows", 4)

    def clean(self):
        data = super().clean()
        company = data.get("company")
        # Every company-owned relation must belong to the selected tenant.
        for name, value in list(data.items()):
            if name == "company" or not hasattr(value, "company_id"):
                continue
            if value.company_id and (not company or value.company_id != company.pk):
                self.add_error(name, "Select a record belonging to this company.")
        if not self.instance._state.adding and getattr(self.instance, "company_id", None) and "company" in self.changed_data:
            self.add_error("company", "Existing records cannot be moved to another company.")
        return data


class CompanyForm(AdminModelForm):
    class Meta:
        model = Company
        fields = [
            "name", "legal_name", "slug", "email", "phone", "gstin",
            "address", "city", "state", "country", "pincode", "currency",
            "logo", "plan", "is_active",
        ]


class UserForm(AdminModelForm):
    password = forms.CharField(
        required=False,
        help_text="New users receive a secure password setup email. On edit, leave blank to keep the password.",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance._state.adding:
            self.fields["password"].widget = forms.HiddenInput()
            self.fields["email_verified"].disabled = True

    class Meta:
        model = User
        fields = [
            "email", "name", "phone", "company", "branch", "role",
            "avatar", "is_active", "is_staff", "is_superuser", "email_verified",
        ]

    def clean(self):
        data = super().clean()
        password = data.get("password")
        if self.instance._state.adding and password:
            self.add_error("password", "New users must set their own password using their invitation email.")
        elif password:
            candidate = User(email=data.get("email", ""), name=data.get("name", ""))
            try:
                validate_password(password, candidate)
            except forms.ValidationError as exc:
                self.add_error("password", exc)
        actor = getattr(self, 'actor', None)
        role = data.get('role')
        if actor and role and not can_delegate(actor, role.permissions):
            self.add_error('role', 'You cannot assign permissions beyond your own role.')
        company = data.get("company")
        branch = data.get("branch")
        role = data.get("role")
        if branch and company and branch.company_id != company.id:
            self.add_error("branch", "Selected branch does not belong to this company.")
        if role and role.company_id and company and role.company_id != company.id:
            self.add_error("role", "Selected role does not belong to this company.")
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if user._state.adding:
            user.set_unusable_password()
            user.email_verified = False
        elif password:
            user.set_password(password)
        if commit:
            user.save()
            self.save_m2m()
        return user


class SubscriptionForm(AdminModelForm):
    class Meta:
        model = Subscription
        fields = [
            "company", "branch", "plan_definition", "plan", "status", "billing_cycle",
            "amount", "currency", "starts_at", "renews_at", "seats",
        ]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "renews_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def clean(self):
        data = super().clean()
        company = data.get("company")
        branch = data.get("branch")
        if branch and company and branch.company_id != company.id:
            self.add_error("branch", "Selected branch does not belong to this company.")
        plan = data.get("plan_definition")
        if plan:
            if not plan.is_active:
                self.add_error("plan_definition", "Select an active plan.")
            cycle = data.get("billing_cycle")
            if cycle not in ("monthly", "yearly"):
                self.add_error("billing_cycle", "Choose monthly or yearly billing.")
            else:
                data["plan"] = plan.name
                data["seats"] = plan.max_users
                data["amount"] = plan.price_for(cycle)
        return data


class CustomerForm(AdminModelForm):
    class Meta:
        model = Customer
        fields = [
            "company", "branch", "name", "phone", "whatsapp", "email",
            "customer_type", "company_name", "gstin", "address", "city",
            "state", "pincode", "notes", "credit_limit", "status",
            "last_service_date", "next_reminder_at",
        ]
        widgets = {
            "last_service_date": forms.DateInput(attrs={"type": "date"}),
            "next_reminder_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def clean(self):
        data = super().clean()
        company = data.get("company")
        branch = data.get("branch")
        if branch and company and branch.company_id != company.id:
            self.add_error("branch", "Selected branch does not belong to this company.")
        return data


class JobForm(AdminModelForm):
    class Meta:
        model = Job
        fields = [
            "company", "branch", "job_number", "customer", "vehicle",
            "advisor", "technician", "status", "priority", "odometer",
            "fuel_level", "promised_at", "delivered_at", "complaints",
            "inspection", "work", "qc", "parts_workflow", "notes",
            "estimate_total", "labour_total", "parts_total",
        ]
        widgets = {
            "promised_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "delivered_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def clean(self):
        data = super().clean()
        company = data.get("company")
        branch = data.get("branch")
        customer = data.get("customer")
        vehicle = data.get("vehicle")
        advisor = data.get("advisor")
        technician = data.get("technician")

        if branch and company and branch.company_id != company.id:
            self.add_error("branch", "Selected branch does not belong to this company.")
        if customer and company and customer.company_id != company.id:
            self.add_error("customer", "Selected customer does not belong to this company.")
        if vehicle and company and vehicle.company_id != company.id:
            self.add_error("vehicle", "Selected vehicle does not belong to this company.")
        if customer and vehicle and vehicle.customer_id != customer.id:
            self.add_error("vehicle", "Selected vehicle does not belong to this customer.")
        if advisor and company and advisor.company_id and advisor.company_id != company.id:
            self.add_error("advisor", "Selected advisor belongs to another company.")
        if technician and company and technician.company_id and technician.company_id != company.id:
            self.add_error("technician", "Selected technician belongs to another company.")
        return data


class StockItemForm(AdminModelForm):
    class Meta:
        model = StockItem
        fields = [
            "company", "branch", "name", "sku", "barcode", "category",
            "brand", "compatible_vehicle", "unit", "cost_price",
            "selling_price", "on_hand", "reserved", "minimum_stock",
            "reorder_level", "rack", "supplier", "tax", "hsn_code", "status",
        ]

    def clean(self):
        data = super().clean()
        company = data.get("company")
        branch = data.get("branch")
        category = data.get("category")
        supplier = data.get("supplier")
        if branch and company and branch.company_id != company.id:
            self.add_error("branch", "Selected branch does not belong to this company.")
        if category and company and category.company_id != company.id:
            self.add_error("category", "Selected category does not belong to this company.")
        if supplier and company and supplier.company_id != company.id:
            self.add_error("supplier", "Selected supplier does not belong to this company.")
        return data


class InvoiceForm(AdminModelForm):
    class Meta:
        model = Invoice
        fields = [
            "company", "branch", "number", "kind", "invoice_type", "status",
            "date", "customer", "vehicle", "job", "tax_mode", "cgst_rate",
            "sgst_rate", "igst_rate", "payment_type", "terms_conditions",
            "staff_name", "notes", "discount", "adjustment", "taxable",
            "cgst", "sgst", "igst", "total", "paid", "balance",
            "payment_mode", "payment_terms",
        ]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        data = super().clean()
        company = data.get("company")
        branch = data.get("branch")
        customer = data.get("customer")
        vehicle = data.get("vehicle")
        job = data.get("job")
        if branch and company and branch.company_id != company.id:
            self.add_error("branch", "Selected branch does not belong to this company.")
        if customer and company and customer.company_id != company.id:
            self.add_error("customer", "Selected customer does not belong to this company.")
        if vehicle and company and vehicle.company_id != company.id:
            self.add_error("vehicle", "Selected vehicle does not belong to this company.")
        if job and company and job.company_id != company.id:
            self.add_error("job", "Selected job does not belong to this company.")
        if customer and vehicle and vehicle.customer_id != customer.id:
            self.add_error("vehicle", "Selected vehicle does not belong to this customer.")
        if job and customer and job.customer_id != customer.id:
            self.add_error("job", "Selected job does not belong to this customer.")
        if job and vehicle and job.vehicle_id != vehicle.id:
            self.add_error("job", "Selected job does not belong to this vehicle.")
        return data


class EmployeeForm(AdminModelForm):
    class Meta:
        model = Employee
        fields = [
            "company", "branch", "user", "employee_code", "name", "phone",
            "email", "designation", "role_name", "department_name",
            "shift_label", "payment_type", "notes", "team", "shift",
            "joining_date", "employment_type", "base_salary", "status",
            "address", "emergency_contact",
        ]
        widgets = {"joining_date": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        data = super().clean()
        company = data.get("company")
        branch = data.get("branch")
        user = data.get("user")
        team = data.get("team")
        shift = data.get("shift")

        if branch and company and branch.company_id != company.id:
            self.add_error("branch", "Selected branch does not belong to this company.")
        if user and company and user.company_id and user.company_id != company.id:
            self.add_error("user", "Selected user belongs to another company.")
        if team and company and team.company_id != company.id:
            self.add_error("team", "Selected team belongs to another company.")
        if shift and company and shift.company_id != company.id:
            self.add_error("shift", "Selected shift belongs to another company.")
        return data



class StaffInviteForm(EmployeeForm):
    """Add a workshop staff account with a private email password invitation.

    Unlike legacy staff profiles, newly-created Control Panel staff receive
    an actual company-scoped login instead of an unlinked Employee row.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Never attach an existing (possibly more privileged) account by ID.
        self.fields.pop("user", None)
        self.fields["email"].required = True
        self.fields["role_name"].required = True

    def clean(self):
        data = super().clean()
        company = data.get("company")
        email = (data.get("email") or "").strip().lower()
        role_name = (data.get("role_name") or "").strip()
        self.login_role = None

        if email and User.objects.filter(email__iexact=email).exists():
            self.add_error("email", "This email already has a login account.")
        if email and company and Employee.objects.filter(
            company=company, email__iexact=email,
        ).exists():
            self.add_error("email", "This staff email is already in use.")

        if company and role_name:
            self.login_role = Role.objects.filter(
                company=company, name__iexact=role_name,
                is_active=True, is_system=False,
            ).first()
            if not self.login_role:
                self.add_error("role_name", "Choose an active company staff role.")
        if data.get("status", "Active") != "Active":
            self.add_error("status", "Create an active staff account to send a login invitation.")
        data["email"] = email
        return data

    def save(self, commit=True):
        if not commit:
            raise ValueError("Staff accounts must be created atomically.")
        from django.db import transaction

        with transaction.atomic():
            employee = super().save(commit=False)
            user = User.objects.create_user(
                email=self.cleaned_data["email"],
                password=None,
                name=employee.name,
                company=employee.company,
                branch=employee.branch,
                role=self.login_role,
                is_staff=False,
                is_superuser=False,
                is_active=True,
                email_verified=False,
            )
            employee.user = user
            employee.save()
            self.save_m2m()
        return employee


class BranchForm(AdminModelForm):
    class Meta:
        model = Branch
        fields = ['company', 'name', 'code', 'phone', 'email', 'address', 'city', 'state', 'pincode', 'is_head_office', 'is_active']


class VehicleForm(AdminModelForm):
    class Meta:
        model = Vehicle
        fields = ['company', 'branch', 'customer', 'registration', 'make', 'model', 'variant', 'year', 'fuel_type', 'transmission', 'color', 'odometer', 'vin', 'engine_no', 'last_service_date', 'next_service_due', 'insurance_expiry', 'notes', 'status']
        widgets = {name: forms.DateInput(attrs={'type': 'date'}) for name in ['last_service_date', 'next_service_due', 'insurance_expiry']}


class RoleForm(AdminModelForm):
    permissions = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple,
        help_text="Select required actions. Panel permissions are for platform staff; workshop permissions are company scoped.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.roles.permission_catalog import TENANT_PERMISSIONS, PANEL_PERMISSIONS
        current = self.instance.permissions or []
        if not isinstance(current, list):
            current = []
        choices = sorted(set(TENANT_PERMISSIONS + PANEL_PERMISSIONS + [v for v in current if isinstance(v, str)]))
        self.fields['permissions'].choices = [('*', 'Full access (administrator only)')] + [(code, code) for code in choices if code != '*']
        self.fields['permissions'].widget.attrs['class'] = 'permission-checkboxes'

    class Meta:
        model = Role
        fields = ['company', 'name', 'code', 'permissions', 'is_active']

    def clean_permissions(self):
        permissions = self.cleaned_data.get('permissions') or []
        if not isinstance(permissions, list) or any(not isinstance(value, str) for value in permissions):
            raise forms.ValidationError('Enter a JSON list of permission codes.')
        actor = getattr(self, 'actor', None)
        if actor and not can_delegate(actor, permissions):
            raise forms.ValidationError('You cannot grant permissions beyond your own role.')
        return permissions


class PlanBuilderForm(AdminModelForm):
    enabled_features = forms.ModelMultipleChoiceField(
        queryset=PlanFeature.objects.filter(is_active=True).select_related("group"),
        widget=forms.CheckboxSelectMultiple, required=False,
        label="Enabled features (all other listed features stay OFF)",
    )

    class Meta:
        model = SubscriptionPlan
        fields = ["name", "description", "monthly_price", "yearly_price",
                  "promo_percent", "promo_ends_at", "max_branches", "max_users",
                  "included_storage_gb", "extra_storage_rate", "trial_days",
                  "sort_order", "is_public", "is_active"]
        widgets = {"promo_ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        groups = {}
        for feature in self.fields["enabled_features"].queryset:
            group_label = (feature.group.parent.label + " / " if feature.group.parent_id else "") + feature.group.label
            groups.setdefault(group_label, []).append((feature.pk, feature.label))
        self.fields["enabled_features"].choices = list(groups.items())
        if self.instance.pk:
            self.fields["enabled_features"].initial = list(
                self.instance.feature_access.filter(enabled=True).values_list("feature_id", flat=True)
            )

    def clean_promo_percent(self):
        discount = self.cleaned_data["promo_percent"]
        if discount > 100:
            raise forms.ValidationError("Promotion cannot exceed 100 percent.")
        return discount

    def save(self, commit=True):
        # Plan keys are stable identifiers for subscriptions and API clients.
        # Generate once from the display name; never change them on edit.
        if self.instance._state.adding:
            base = slugify(self.cleaned_data["name"])[:80].strip("-_") or "plan"
            key = base
            number = 2
            while SubscriptionPlan.objects.filter(key=key).exists():
                suffix = f"-{number}"
                key = f"{base[:80 - len(suffix)]}{suffix}"
                number += 1
            self.instance.key = key
        obj = super().save(commit=commit)
        if commit:
            selected = set(self.cleaned_data["enabled_features"].values_list("pk", flat=True))
            PlanFeatureAccess.objects.filter(plan=obj).delete()
            PlanFeatureAccess.objects.bulk_create([
                PlanFeatureAccess(plan=obj, feature_id=pk, enabled=True) for pk in selected
            ])
        return obj


class FeatureGroupForm(AdminModelForm):
    class Meta:
        model = FeatureGroup
        fields = ["key", "label", "parent", "sort_order", "is_active"]


class PlanFeatureForm(AdminModelForm):
    class Meta:
        model = PlanFeature
        fields = ["key", "label", "group", "description", "sort_order", "is_active"]
