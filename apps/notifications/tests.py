from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.credits.models import FeatureBundle
from apps.customers.models import Customer
from apps.jobs.models import Job, JobActivity
from apps.notifications.job_events import notify_job_event
from apps.notifications.models import Notification
from apps.roles.models import Role
from apps.saas.models import CompanySetting, FeatureGroup, PlanFeature
from apps.vehicles.models import Vehicle


class WorkshopJobNotificationsTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Workshop Alpha", slug="workshop-alpha")
        self.branch = Branch.objects.create(company=self.company, name="Main", code="MAIN")
        self.other_branch = Branch.objects.create(company=self.company, name="Other", code="OTHER")
        self.full_role = Role.objects.create(
            company=self.company, name="Workshop Staff", code="STAFF",
            permissions=["jobs.view", "notifications.view"],
        )
        self.restricted_role = Role.objects.create(
            company=self.company, name="Restricted", code="RESTRICTED",
            permissions=["notifications.view"],
        )
        self.owner = User.objects.create(
            email="owner@example.test", name="Owner", company=self.company,
            role=self.full_role, is_active=True,
        )
        self.company.owner = self.owner
        self.company.save(update_fields=["owner"])
        self.mechanic = User.objects.create(
            email="mechanic@example.test", name="Mechanic", company=self.company,
            branch=self.branch, role=self.full_role, is_active=True,
        )
        self.other_staff = User.objects.create(
            email="other@example.test", name="Other Branch", company=self.company,
            branch=self.other_branch, role=self.full_role, is_active=True,
        )
        self.restricted = User.objects.create(
            email="restricted@example.test", name="Restricted", company=self.company,
            branch=self.branch, role=self.restricted_role, is_active=True,
        )
        customer = Customer.objects.create(
            company=self.company, branch=self.branch,
            name="Demo Customer", phone="9876543210",
        )
        vehicle = Vehicle.objects.create(
            company=self.company, branch=self.branch, customer=customer,
            registration="KL10AA1234",
        )
        self.job = Job.objects.create(
            company=self.company, branch=self.branch, customer=customer,
            vehicle=vehicle, job_number="JOB-TEST-001", advisor=self.owner,
        )
        self.receipt = JobActivity.objects.create(
            company=self.company, branch=self.branch, job=self.job,
            event="Job Created", description="Test", actor=self.owner,
        )

    def test_new_vehicle_notifies_only_authorized_workshop_staff(self):
        count = notify_job_event(self.job, "job_created", self.receipt)
        self.assertEqual(count, 2)
        self.assertSetEqual(
            set(Notification.objects.filter(notification_type="job_created")
                .values_list("user_id", flat=True)),
            {self.owner.pk, self.mechanic.pk},
        )

    def test_one_event_does_not_create_duplicate_messages(self):
        notify_job_event(self.job, "job_created", self.receipt)
        self.assertEqual(notify_job_event(self.job, "job_created", self.receipt), 0)
        self.assertEqual(Notification.objects.count(), 2)

    def test_assignment_only_notifies_assigned_mechanic(self):
        self.job.technician = self.mechanic
        self.job.save(update_fields=["technician"])
        count = notify_job_event(self.job, "job_assigned", self.receipt)
        self.assertEqual(count, 1)
        notice = Notification.objects.get(notification_type="job_assigned")
        self.assertEqual(notice.user_id, self.mechanic.pk)
        self.assertEqual(notice.data["route"], f"/jobs/{self.job.pk}/work")

    def test_settings_can_disable_new_vehicle_alerts(self):
        CompanySetting.objects.create(
            company=self.company, category="notifications",
            data={"jobCreated": False},
        )
        self.assertEqual(notify_job_event(self.job, "job_created", self.receipt), 0)
        self.assertFalse(Notification.objects.exists())

    def test_read_state_is_private_to_staff_member(self):
        notify_job_event(self.job, "job_created", self.receipt)
        Notification.objects.filter(user=self.mechanic).update(is_read=True)
        self.assertFalse(Notification.objects.get(user=self.owner).is_read)

    def test_ready_for_delivery_has_high_priority_and_job_link(self):
        self.job.status = Job.STATUS_READY
        self.job.save(update_fields=["status"])
        notify_job_event(self.job, "job_ready", self.receipt)
        notice = Notification.objects.get(user=self.mechanic)
        self.assertEqual(notice.data["priority"], "High")
        self.assertEqual(notice.data["route"], f"/jobs/{self.job.pk}")

    @override_settings(CREDIT_LABEL_ONLY_ACCESS_ENABLED=True, CREDIT_FEATURE_ENFORCEMENT_ENABLED=True)
    def test_notifications_are_free_for_authorized_staff_in_label_only_mode(self):
        self.company.require_paid_features = True
        self.company.save(update_fields=["require_paid_features"])
        private = Notification.objects.create(
            company=self.company, branch=self.branch, user=self.restricted, title="Your notification",
        )
        Notification.objects.create(
            company=self.company, branch=self.branch, user=self.mechanic, title="Another person's notification",
        )
        client = APIClient()
        client.force_authenticate(self.restricted)
        access = client.get("/api/v1/saas/credits/access")
        self.assertEqual(access.status_code, 200, access.data)
        self.assertIn("notifications", access.data["freeModules"])
        self.assertEqual(access.data["activeLabelModules"], [])
        self.assertEqual(access.data["enabledModules"], [])
        inbox = client.get("/api/v1/notifications")
        self.assertEqual(inbox.status_code, 200, inbox.data)
        results = inbox.data["results"] if isinstance(inbox.data, dict) else inbox.data
        self.assertEqual([row["id"] for row in results], [str(private.pk)])

        self.restricted_role.permissions = []
        self.restricted_role.save(update_fields=["permissions"])
        self.assertEqual(client.get("/api/v1/notifications").status_code, 403)

    def test_notifications_are_excluded_from_workshop_operations_pricing(self):
        group = FeatureGroup.objects.create(key="test-notifications", label="Test Notifications")
        notifications, _ = PlanFeature.objects.update_or_create(
            key="notifications", defaults={"group": group, "label": "Notifications", "is_active": True},
        )
        operations, _ = PlanFeature.objects.update_or_create(
            key="workshop_operations", defaults={"group": group, "label": "Workshop Operations", "is_active": True},
        )
        jobs, _ = PlanFeature.objects.get_or_create(key="jobs", defaults={"group": group, "label": "Jobs"})
        bundle = FeatureBundle.objects.create(key="test-workshop-operations", name="Workshop Operations", price_credits=10)
        bundle.features.add(operations, notifications)
        legacy = FeatureBundle.objects.create(key="test-paid-notifications", name="Notifications", price_credits=5)
        legacy.features.add(notifications)
        legacy_mixed = FeatureBundle.objects.create(key="test-old-jobs-bundle", name="Jobs and Notifications", price_credits=5)
        legacy_mixed.features.add(jobs, notifications)

        client = APIClient()
        client.force_authenticate(self.owner)
        catalog = client.get("/api/v1/saas/credits/bundles")
        self.assertEqual(catalog.status_code, 200, catalog.data)
        offered_ids = {row["id"] for row in catalog.data}
        self.assertNotIn(legacy.pk, offered_ids)
        self.assertNotIn(legacy_mixed.pk, offered_ids)
        self.assertNotIn("notifications", [row["key"] for row in catalog.data])
        workshop = next(row for row in catalog.data if row["id"] == bundle.pk)
        self.assertNotIn("notifications", workshop["includedModuleKeys"])
        self.assertIn("jobs", workshop["includedModuleKeys"])
