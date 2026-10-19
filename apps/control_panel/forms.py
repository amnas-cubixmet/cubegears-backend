from decimal import Decimal
from pathlib import Path

from django import forms
from django.utils.text import slugify
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password

from apps.accounts.models import User
from apps.accounts.permissions import can_delegate
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.employees.models import Employee
from apps.roles.models import Role
from apps.saas.models import SubscriptionPlan, FeatureGroup, PlanFeature, PlanFeatureAccess
from apps.credits.models import FeatureBundle


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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance._state.adding:
            self.fields["owner"].queryset = User.objects.none()
            self.fields["require_paid_features"].initial = True
        else:
            self.fields["owner"].queryset = User.objects.filter(company=self.instance, is_active=True)

    def clean_owner(self):
        owner = self.cleaned_data.get("owner")
        if owner and (owner.company_id != self.instance.pk or not owner.is_active):
            raise forms.ValidationError("Choose an active user of this company.")
        return owner

    """Create a stable unique tenant slug and safely accept an optional logo image."""

    class Meta:
        model = Company
        fields = [
            "name", "legal_name", "email", "phone", "gstin",
            "address", "city", "state", "country", "pincode", "currency",
            "logo_image", "plan", "is_active", "owner", "require_paid_features",
        ]
        widgets = {
            "logo_image": forms.FileInput(attrs={
                "accept": "image/jpeg,image/png,image/webp",
                "data-company-logo": "true",
            }),
        }
        labels = {"logo_image": "Company Logo"}
        help_texts = {
            "name": "The company slug will be generated automatically.",
            "logo_image": "JPG, PNG or WebP only. Maximum file size: 2 MB.",
        }

    def clean_logo_image(self):
        upload = self.cleaned_data.get("logo_image")
        if not upload or not hasattr(upload, "image"):
            # Existing image on an edit: retain it without re-uploading.
            return upload
        if upload.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Logo must be 2 MB or smaller.")
        extension = Path(upload.name).suffix.lower()
        extensions = {"JPEG": {".jpg", ".jpeg"}, "PNG": {".png"}, "WEBP": {".webp"}}
        if extension not in extensions.get(upload.image.format, set()):
            raise forms.ValidationError("File extension must match a JPG, PNG or WebP image.")
        if upload.image.width > 4096 or upload.image.height > 4096:
            raise forms.ValidationError("Logo image dimensions must not exceed 4096 × 4096.")
        return upload

    def save(self, commit=True):
        company = super().save(commit=False)
        if self.instance._state.adding:
            # Never trust a client-submitted slug. Existing slugs stay stable on edit.
            base = slugify(company.name, allow_unicode=False)[:160].strip("-") or "company"
            proposed = base
            suffix = 2
            while Company.objects.filter(slug=proposed).exclude(pk=company.pk).exists():
                proposed = f"{base[:180 - len(str(suffix)) - 1]}-{suffix}"
                suffix += 1
            company.slug = proposed
        if commit:
            company.save()
            self.save_m2m()
        return company


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
        if role and role.company_id != (company.pk if company else None):
            self.add_error("role", "Role scope must match the user's company.")
        if role and not role.is_active:
            self.add_error("role", "Select an active login role.")
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
        if user and company and user.company_id != company.id:
            self.add_error("user", "Selected user must belong to this workshop company.")
        if user and (user.is_staff or user.is_superuser):
            self.add_error("user", "A SaaS platform administrator cannot be linked as workshop staff.")
        if team and company and team.company_id != company.id:
            self.add_error("team", "Selected team belongs to another company.")
        if shift and company and shift.company_id != company.id:
            self.add_error("shift", "Selected shift belongs to another company.")
        return data



class PlatformStaffInviteForm(AdminModelForm):
    """Invite companyless, explicitly role-scoped SaaS Control Panel staff."""

    class Meta:
        model = User
        fields = ("name", "email", "phone", "role")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = Role.objects.filter(
            company__isnull=True, is_active=True, is_system=False,
        ).order_by("name")
        self.fields["role"].required = True
        self.fields["role"].empty_label = "Select a SaaS Control Panel role"
        self.fields["role"].help_text = (
            "Only companyless platform roles are available. "
            "Manage their individual permissions under Roles."
        )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("This email already has a login account.")
        return email

    def clean_role(self):
        role = self.cleaned_data["role"]
        permissions = role.permissions
        if role.company_id is not None or not role.is_active or role.is_system:
            raise forms.ValidationError("Select an active SaaS platform staff role.")
        from apps.roles.permission_catalog import PANEL_PERMISSIONS
        if (not isinstance(permissions, list)
                or not all(isinstance(code, str) for code in permissions)
                or not any(code == "*" or (
                    code in PANEL_PERMISSIONS and code.endswith(".view")
                ) for code in permissions)):
            raise forms.ValidationError(
                "This role needs at least one valid SaaS Control Panel view permission."
            )
        actor = getattr(self, "actor", None)
        if not actor or (not actor.is_superuser and not can_delegate(actor, permissions)):
            raise forms.ValidationError("You cannot assign permissions beyond your own role.")
        return role

    def save(self, commit=True):
        if not commit:
            raise ValueError("Platform staff must be saved atomically.")
        user = super().save(commit=False)
        user.set_unusable_password()
        user.company = None
        user.branch = None
        user.is_staff = True
        user.is_superuser = False
        user.is_active = True
        user.email_verified = False
        user.save()
        self.save_m2m()
        return user


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
            elif (not getattr(self, "actor", None)
                  or not can_delegate(self.actor, self.login_role.permissions)):
                self.add_error(
                    "role_name",
                    "You cannot assign a workshop role beyond your own permissions.",
                )
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


class RoleForm(AdminModelForm):
    permissions = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple,
        help_text="Select required actions. Panel permissions are for platform staff; workshop permissions are company scoped.")

    def __init__(self, *args, **kwargs):
        # A dedicated URL, not a user-editable POST field, fixes the role scope.
        self.scope = kwargs.pop("scope", None)
        super().__init__(*args, **kwargs)
        from apps.roles.permission_catalog import TENANT_PERMISSIONS, PANEL_PERMISSIONS
        if not self.instance._state.adding:
            # Editing a role may never transfer authority between companies
            # or between workshop and SaaS platform accounts.
            self.fields["company"].disabled = True
            if self.instance.company_id is None:
                self.fields["company"].widget = forms.HiddenInput()
        elif self.scope == "platform":
            self.fields["company"].required = False
            self.fields["company"].disabled = True
            self.fields["company"].widget = forms.HiddenInput()
        elif self.scope == "workshop":
            self.fields["company"].required = True
            self.fields["company"].help_text = "Select the workshop this role belongs to."
        current = self.instance.permissions or []
        if not isinstance(current, list):
            current = []
        choices = sorted(set(TENANT_PERMISSIONS + PANEL_PERMISSIONS + [v for v in current if isinstance(v, str)]))
        self.fields['permissions'].choices = [('*', 'Full access (administrator only)')] + [(code, code) for code in choices if code != '*']
        self.fields['permissions'].widget.attrs['class'] = 'permission-checkboxes'

    @property
    def permission_sections(self):
        """Server-rendered, bound-form-safe matrix for the permission editor."""
        from apps.roles.permission_catalog import TENANT_PERMISSIONS, PANEL_PERMISSIONS

        selected = (
            set(self.data.getlist("permissions"))
            if self.is_bound
            else set(self.instance.permissions or [])
        )
        sections = []
        for scope, title, catalog in (
            ("platform", "SaaS Control Panel", PANEL_PERMISSIONS),
            ("workshop", "Workshop / Company", TENANT_PERMISSIONS),
        ):
            if self.scope in {"platform", "workshop"} and scope != self.scope:
                continue
            modules = {}
            for code in catalog:
                module_code, action = code.rsplit(".", 1)
                module_code = module_code.removeprefix("panel.")
                if module_code not in modules:
                    modules[module_code] = {
                        "key": module_code,
                        "label": module_code.replace("_", " ").title(),
                    }
                modules[module_code][action] = {
                    "code": code,
                    "checked": code in selected,
                }
            sections.append({
                "scope": scope,
                "title": title,
                "modules": [modules[key] for key in sorted(modules)],
            })
        return sections

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

    def clean(self):
        data = super().clean()
        company = data.get("company")
        permissions = data.get("permissions") or []
        from apps.roles.permission_catalog import PANEL_PERMISSIONS
        if self.scope == "platform":
            if self.data.get("company"):
                self.add_error("company", "SaaS Admin Staff roles cannot belong to a workshop.")
            if any(code != "*" and code not in PANEL_PERMISSIONS for code in permissions):
                self.add_error("permissions", "Choose only valid SaaS Control Panel permissions.")
            if not any(code == "*" or (code in PANEL_PERMISSIONS and code.endswith(".view")) for code in permissions):
                self.add_error(
                    "permissions", "Assign at least one SaaS view permission so staff can sign in.",
                )
        elif self.scope == "workshop" and not company:
            self.add_error("company", "Select a workshop company for this role.")
        if company and any(code.startswith("panel.") for code in permissions):
            self.add_error(
                "permissions", "Workshop roles cannot contain SaaS platform permissions."
            )
        if not company and any(
            code != "*" and not code.startswith("panel.") for code in permissions
        ):
            self.add_error(
                "permissions", "SaaS platform roles cannot contain workshop permissions."
            )
        return data


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
                  "included_storage_gb", "extra_storage_rate", "trial_days", "access_duration_days", "checkout_price",
                  "sort_order", "is_public", "is_active"]
        widgets = {
            "promo_ends_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}
            ),
        }
        help_texts = {
            "promo_percent": "Optional catalog discount between 0 and 100 percent.",
            "promo_ends_at": "Leave blank for an ongoing promotion.",
            "checkout_price": "One-time INR amount charged through verified Razorpay checkout.",
            "access_duration_days": "Days of access after verified payment (1 to 730).",
            "extra_storage_rate": "Configured price for each additional GB.",
            "sort_order": "Lower numbers appear earlier in the catalog.",
        }

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

    def clean_checkout_price(self):
        price = self.cleaned_data["checkout_price"]
        if price < 0 or price > 10000000:
            raise forms.ValidationError("Enter a valid one-time plan price.")
        return price

    def clean_access_duration_days(self):
        days = self.cleaned_data["access_duration_days"]
        if days < 1 or days > 730:
            raise forms.ValidationError("Plan validity must be between 1 and 730 days.")
        return days

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


class FeatureBundleForm(AdminModelForm):
    """Five fields only: charge exclusively by active company login users."""
    features = forms.ModelMultipleChoiceField(
        label="Included existing features",
        queryset=PlanFeature.objects.filter(is_active=True),
        widget=forms.CheckboxSelectMultiple(),
        help_text="Choose one or more existing features. All active published modules are listed.",
    )

    class Meta:
        model = FeatureBundle
        fields = ["name", "description", "features", "per_user_credits", "billing_type"]
        labels = {"per_user_credits": "Per user credits"}
        help_texts = {
            "per_user_credits": "Credits per active login account for each selected billing period.",
        }

    def clean_per_user_credits(self):
        price = self.cleaned_data["per_user_credits"]
        if price is None or not Decimal("0.01") <= price <= Decimal("1000000.00"):
            raise forms.ValidationError("Enter 0.01 to 1,000,000 credits per user.")
        return price

    def clean_features(self):
        features = self.cleaned_data["features"]
        if features.count() < 1:
            raise forms.ValidationError("Select at least one existing feature for this label.")
        if self.instance.pk and self.instance.company_access.exists():
            previous = set(self.instance.features.values_list("pk", flat=True))
            selected = set(features.values_list("pk", flat=True))
            if not previous.issubset(selected):
                raise forms.ValidationError(
                    "Cannot remove features from a label already purchased by companies. Create a new label instead."
                )
        return features

    def clean_billing_type(self):
        period = self.cleaned_data["billing_type"]
        if (self.instance.pk and self.instance.company_access.exists()
                and period != self.instance.billing_type):
            raise forms.ValidationError(
                "Cannot change the billing period after a company purchases this label. Create a new label."
            )
        return period

    def save(self, commit=True):
        if self.instance._state.adding:
            base = slugify(self.cleaned_data["name"])[:100].strip("-_") or "label"
            key, number = base, 2
            while FeatureBundle.objects.filter(key=key).exists():
                suffix = f"-{number}"
                key = f"{base[:100 - len(suffix)]}{suffix}"
                number += 1
            self.instance.key = key
        # Legacy model columns remain for existing transactions, but new
        # purchases use per-user credits exclusively.
        self.instance.price_credits = Decimal("0.00")
        self.instance.activation_credits = Decimal("0.00")
        return super().save(commit=commit)
