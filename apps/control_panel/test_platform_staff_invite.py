"""SaaS admin staff invitations and cross-tenant role security regressions."""
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.permissions import user_has_permission
from apps.companies.models import Company
from apps.control_panel.forms import RoleForm, UserForm
from apps.roles.models import Role


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://workshop.example.com",
)
class PlatformStaffInvitationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            email="owner-platform@example.com", name="Owner",
            password="PlatformOwnerSecure2026!",
        )
        self.client.force_login(self.owner)
        self.panel_role = Role.objects.create(
            name="SaaS Admin Staff", code="SAAS_TEAM",
            company=None, permissions=["panel.dashboard.view", "panel.users.view"],
        )
        self.workshop = Company.objects.create(name="Garage", slug="garage-platform-test")
        self.tenant_role = Role.objects.create(
            name="Mechanic", code="MECHANIC", company=self.workshop,
            permissions=["jobs.view"],
        )

    def payload(self, **changes):
        data = {
            "account_type": "platform",
            "name": "New SaaS Staff",
            "email": "platform-new@example.com",
            "phone": "9876543210",
            "role": str(self.panel_role.pk),
        }
        data.update(changes)
        return data

    def test_saas_staff_form_is_an_option_from_staff_add(self):
        page = self.client.get(reverse("panel:staff-create"))
        self.assertContains(page, "SaaS Admin Staff")
        page = self.client.get(reverse("panel:staff-create") + "?type=platform")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="account_type"')
        self.assertContains(page, 'value="platform"')
        self.assertContains(page, 'name="role"')
        self.assertNotContains(page, 'name="company"')
        self.assertNotContains(page, 'name="is_superuser"')

    def test_platform_staff_creation_emails_secure_setup_link(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("panel:staff-create"), self.payload(),
            )
        self.assertRedirects(response, reverse("panel:staff"))
        member = User.objects.get(email="platform-new@example.com")
        self.assertIsNone(member.company_id)
        self.assertIsNone(member.branch_id)
        self.assertEqual(member.role_id, self.panel_role.pk)
        self.assertTrue(member.is_staff)
        self.assertFalse(member.is_superuser)
        self.assertFalse(member.email_verified)
        self.assertFalse(member.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [member.email])
        self.assertIn("/login/password-setup/", mail.outbox[0].body)
        listing = self.client.get(reverse("panel:staff"))
        self.assertContains(listing, "SaaS Admin Staff")
        self.assertContains(listing, "platform-new@example.com")
        self.assertTrue(user_has_permission(member, "panel.dashboard.view"))
        self.assertFalse(user_has_permission(member, "jobs.view"))

    def test_tenant_role_cannot_be_assigned_to_platform_staff(self):
        response = self.client.post(
            reverse("panel:staff-create"),
            self.payload(role=str(self.tenant_role.pk)),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")
        self.assertFalse(User.objects.filter(email="platform-new@example.com").exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_duplicate_platform_staff_email_is_rejected_case_insensitively(self):
        response = self.client.post(
            reverse("panel:staff-create"),
            self.payload(email="OWNER-PLATFORM@EXAMPLE.COM"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This email already has a login account.")
        self.assertEqual(User.objects.filter(email__iexact="owner-platform@example.com").count(), 1)

    def test_staff_create_permission_alone_does_not_grant_platform_creation(self):
        limited_role = Role.objects.create(
            company=None, name="Workshop Staff Manager", code="WORKSHOP_STAFF_MANAGER",
            permissions=["panel.staff.view", "panel.staff.create"],
        )
        delegated = User.objects.create_user(
            email="manager-platform@example.com", name="Manager",
            password="ManagerSecure2026!", role=limited_role,
            company=None, is_staff=True,
        )
        self.client.force_login(delegated)
        self.assertEqual(self.client.get(reverse("panel:staff-create")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("panel:staff-create") + "?type=platform").status_code,
            403,
        )
        self.assertEqual(
            self.client.post(reverse("panel:staff-create"), self.payload()).status_code,
            403,
        )
        self.assertFalse(User.objects.filter(email="platform-new@example.com").exists())

    def test_cannot_delegate_role_permissions_not_possessed(self):
        limited_role = Role.objects.create(
            company=None, name="Account Creator", code="ACCOUNT_CREATOR",
            permissions=["panel.staff.view", "panel.staff.create", "panel.users.create"],
        )
        delegated = User.objects.create_user(
            email="creator-platform@example.com", name="Creator",
            password="CreatorSecure2026!", role=limited_role,
            company=None, is_staff=True,
        )
        self.client.force_login(delegated)
        response = self.client.post(reverse("panel:staff-create"), self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You cannot assign permissions beyond your own role.")
        self.assertFalse(User.objects.filter(email="platform-new@example.com").exists())

    def test_role_form_does_not_mix_platform_and_workshop_permissions(self):
        platform = RoleForm(data={
            "company": "", "name": "Bad platform", "code": "BAD_PLATFORM",
            "permissions": ["jobs.view"], "is_active": "on",
        })
        platform.actor = self.owner
        self.assertFalse(platform.is_valid())
        self.assertIn("SaaS platform roles cannot contain workshop", str(platform.errors))

        tenant = RoleForm(data={
            "company": str(self.workshop.pk), "name": "Bad tenant",
            "code": "BAD_TENANT", "permissions": ["panel.dashboard.view"],
            "is_active": "on",
        })
        tenant.actor = self.owner
        self.assertFalse(tenant.is_valid())
        self.assertIn("Workshop roles cannot contain SaaS", str(tenant.errors))

    def test_user_form_rejects_mismatched_role_scope(self):
        form = UserForm(data={
            "email": "wrong-scope@example.com",
            "name": "Wrong Scope",
            "company": "",
            "role": str(self.tenant_role.pk),
            "is_active": "on",
        })
        form.actor = self.owner
        self.assertFalse(form.is_valid())
        self.assertIn("Role scope must match", str(form.errors))
