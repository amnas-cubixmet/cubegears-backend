from django import forms
from django.contrib.auth import authenticate

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.employees.models import Employee, Shift, Team
from apps.inventory.models import StockCategory, StockItem, Supplier
from apps.invoices.models import Invoice
from apps.jobs.models import Job
from apps.roles.models import Role
from apps.saas.models import Subscription
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
        help_text="Leave blank to keep the existing password.",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    class Meta:
        model = User
        fields = [
            "email", "name", "phone", "company", "branch", "role",
            "avatar", "is_active", "is_staff", "is_superuser", "email_verified",
        ]

    def clean(self):
        data = super().clean()
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
        if password:
            user.set_password(password)
        elif not user.pk:
            user.set_unusable_password()
        if commit:
            user.save()
            self.save_m2m()
        return user


class SubscriptionForm(AdminModelForm):
    class Meta:
        model = Subscription
        fields = [
            "company", "branch", "plan", "status", "billing_cycle",
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
        return data


class EmployeeForm(AdminModelForm):
    class Meta:
        model = Employee
        fields = [
            "company", "branch", "user", "employee_code", "name", "phone",
            "email", "designation", "role_name", "department_name",
            "shift_label", "payment_type", "notes", "team", "shift", "skills",
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
        skills = data.get("skills")

        if branch and company and branch.company_id != company.id:
            self.add_error("branch", "Selected branch does not belong to this company.")
        if user and company and user.company_id and user.company_id != company.id:
            self.add_error("user", "Selected user belongs to another company.")
        if team and company and team.company_id != company.id:
            self.add_error("team", "Selected team belongs to another company.")
        if shift and company and shift.company_id != company.id:
            self.add_error("shift", "Selected shift belongs to another company.")
        if company and skills:
            for skill in skills:
                if skill.company_id != company.id:
                    self.add_error("skills", "One or more selected skills belong to another company.")
                    break
        return data
