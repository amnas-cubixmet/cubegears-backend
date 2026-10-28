"""Reviewer UI hints never replace the POST endpoint security check."""
from types import SimpleNamespace
from uuid import uuid4

from django.test import SimpleTestCase

from apps.attendance.compat_views import approval_review_fields


class ApprovalReviewEligibilityTests(SimpleTestCase):
    def setUp(self):
        self.user_id = uuid4()
        self.manager = SimpleNamespace(id=self.user_id)
        self.own_employee = SimpleNamespace(user_id=self.user_id)

    def test_own_request_is_blocked_when_attendance_rule_disallows(self):
        result = approval_review_fields(
            self.own_employee,
            self.manager,
            SimpleNamespace(allow_self_approval=False),
        )
        self.assertIs(result["canReview"], False)
        self.assertIn("another authorized manager", result["reviewBlockReason"])

    def test_other_employees_are_reviewable(self):
        result = approval_review_fields(
            SimpleNamespace(user_id=uuid4()),
            self.manager,
            SimpleNamespace(allow_self_approval=False),
        )
        self.assertEqual(result, {"canReview": True, "reviewBlockReason": ""})

    def test_self_review_works_only_when_rule_explicitly_permits_it(self):
        result = approval_review_fields(
            self.own_employee,
            self.manager,
            SimpleNamespace(allow_self_approval=True),
        )
        self.assertEqual(result, {"canReview": True, "reviewBlockReason": ""})

    def test_unlinked_employee_is_not_mistaken_for_logged_in_manager(self):
        result = approval_review_fields(
            SimpleNamespace(user_id=None),
            self.manager,
            SimpleNamespace(allow_self_approval=False),
        )
        self.assertIs(result["canReview"], True)
