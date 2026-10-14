"""User-management UI, real counts, safe filters and user-editor security regressions."""
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.companies.models import Company


class UserManagementUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="users-theme-admin@example.com",
            name="Platform Root",
            password="Platform-secure-test-2026!",
        )
        cls.company = Company.objects.create(name="Alpha Garage", slug="alpha-garage")
        cls.other_company = Company.objects.create(name="Beta Garage", slug="beta-garage")
        cls.staff = User.objects.create_user(
            email="staff@example.com", name="Team Admin", password="Staff-secure-test-123!",
            company=cls.company, is_staff=True, is_active=True, email_verified=True,
        )
        cls.workshop = User.objects.create_user(
            email="worker@example.com", name="Workshop Mechanic", password="Workshop-secure-test-123!",
            company=cls.company, is_active=False,
        )
        cls.other = User.objects.create_user(
            email="other@example.com", name="Other Mechanic", password="Other-secure-test-123!",
            company=cls.other_company, is_active=True,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_user_list_has_live_kpis_and_base_stylesheet(self):
        response = self.client.get(reverse("panel:users"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Users &amp; Access Management")
        self.assertContains(response, "User Directory")
        self.assertContains(response, "management-ui.css")
        self.assertContains(response, "Verified Emails")
        self.assertContains(response, 'class="user-stat-value"', count=4)
        self.assertContains(response, 'class="user-stat-meta"', count=4)
        self.assertContains(response, "Team Admin")
        self.assertContains(response, "Workshop Mechanic")
        self.assertEqual(response.context["total_users"], 4)
        self.assertEqual(response.context["active_users"], 3)
        self.assertEqual(response.context["admin_users"], 2)
        self.assertEqual(response.context["verified_users"], 1)
        self.assertIsNotNone(finders.find("control_panel/management-ui.css"))

    def test_filters_combine_without_mutating_global_counts(self):
        response = self.client.get(reverse("panel:users"), {
            "company": str(self.company.pk), "status": "active",
            "access": "admin", "q": "Team",
        })
        self.assertEqual(response.context["rows"].paginator.count, 1)
        self.assertContains(response, "Team Admin")
        self.assertNotContains(response, "Workshop Mechanic")
        self.assertEqual(response.context["total_users"], 4)
        self.assertEqual(response.context["active_users"], 3)

        disabled = self.client.get(reverse("panel:users"), {
            "company": str(self.company.pk), "status": "disabled", "access": "workshop",
        })
        self.assertEqual(disabled.context["rows"].paginator.count, 1)
        self.assertContains(disabled, "Workshop Mechanic")

        supers = self.client.get(reverse("panel:users"), {"access": "superuser"})
        self.assertEqual(supers.context["rows"].paginator.count, 1)

    def test_bad_query_filters_gracefully_ignored(self):
        result = self.client.get(reverse("panel:users"), {
            "status": "nonexistent", "access": "nonexistent",
        })
        self.assertEqual(result.context["rows"].paginator.count, 4)
        self.assertEqual(result.context["status"], "")
        self.assertEqual(result.context["access"], "")

    def test_user_create_and_edit_get_dedicated_form_without_password_regression(self):
        create = self.client.get(reverse("panel:user-create"))
        self.assertTemplateUsed(create, "control_panel/user_form.html")
        self.assertContains(create, "Secure password invitation")
        self.assertContains(create, 'name="password"', count=1)
        self.assertContains(create, 'type="hidden" name="password"')
        self.assertContains(create, "Company &amp; Role")
        self.assertContains(create, "Super Admin")

        edit = self.client.get(reverse("panel:user-edit", args=[self.staff.pk]))
        self.assertTemplateUsed(edit, "control_panel/user_form.html")
        self.assertContains(edit, 'name="password"', count=1)
        self.assertContains(edit, 'type="password"')
        self.assertContains(edit, "New Password (optional)")

    def test_user_creation_has_unusable_password_until_setup(self):
        result = self.client.post(reverse("panel:user-create"), {
            "email": "created@example.com",
            "name": "New Invitee",
            "is_active": "on",
        })
        self.assertRedirects(result, reverse("panel:users"))
        created = User.objects.get(email="created@example.com")
        self.assertFalse(created.has_usable_password())
        self.assertFalse(created.email_verified)

    def test_permissions_still_restrict_anonymous_and_self_deactivation(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("panel:users")).status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.post(reverse("panel:user-toggle", args=[self.admin.pk])).status_code,
            403,
        )

    def test_user_detail_uses_profile_and_displays_actual_access(self):
        detail = self.client.get(reverse("panel:user-detail", args=[self.staff.pk]))
        self.assertContains(detail, 'class="panel-card user-profile-header"')
        self.assertContains(detail, "Team Admin")
        self.assertContains(detail, "Verified email")
        self.assertContains(detail, "Admin Staff")

    def test_other_resource_forms_keep_generic_template(self):
        result = self.client.get(reverse("panel:customer-create"))
        self.assertTemplateUsed(result, "control_panel/form.html")
        self.assertContains(result, "management-ui.css")
