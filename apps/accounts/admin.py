"""Django administrator onboarding with per-user email password setup."""
from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import MagicLink, User


class InvitedUserCreationForm(forms.ModelForm):
    """Create a database user without generating or exposing a shared password."""

    class Meta:
        model = User
        fields = ("email", "name", "company", "branch", "role", "is_staff", "is_active")

    def clean(self):
        data = super().clean()
        company = data.get("company")
        branch = data.get("branch")
        role = data.get("role")

        if company:
            if not role or role.company_id != company.pk or not role.is_active:
                self.add_error("role", "Select an active role belonging to this company.")
            if branch and branch.company_id != company.pk:
                self.add_error("branch", "Select a branch belonging to this company.")
        elif branch or (role and role.company_id is not None):
            self.add_error("company", "The branch and role must belong to the selected company.")
        elif not data.get("is_staff") or not role or not role.is_active:
            self.add_error("role", "A platform administrator needs staff access and an active platform role.")
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_unusable_password()
        user.email_verified = False
        if commit:
            user.save()
            self.save_m2m()
        return user


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = InvitedUserCreationForm
    ordering = ("email",)
    list_display = ("email", "name", "company", "branch", "role", "is_active", "is_staff")
    search_fields = ("email", "name", "phone")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("name", "phone", "avatar", "company", "branch", "role")}),
        ("Status", {"fields": ("email_verified", "is_active", "is_staff", "is_superuser")}),
        ("Permissions", {"fields": ("groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login",)}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": (
            "email", "name", "company", "branch", "role", "is_staff", "is_active",
        )}),
    )
    filter_horizontal = ("groups", "user_permissions")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.set_unusable_password()
            obj.email_verified = False
        super().save_model(request, obj, form, change)
        if not change:
            # Shared helper chooses the correct platform or company setup URL.
            # It schedules delivery after commit and never emails a password.
            from apps.control_panel.views import _send_new_user_invite

            if _send_new_user_invite(request, obj):
                self.message_user(
                    request,
                    "User created. A password setup invitation was scheduled for their email.",
                    level=messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    "User created without a login invite. Check that the account is "
                    "active and has a valid login role.",
                    level=messages.WARNING,
                )


@admin.register(MagicLink)
class MagicLinkAdmin(admin.ModelAdmin):
    list_display = ("user", "expires_at", "used_at", "created_at")
    readonly_fields = ("token_hash", "created_at")
