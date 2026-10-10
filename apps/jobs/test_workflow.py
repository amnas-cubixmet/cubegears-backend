from types import SimpleNamespace

from django.test import SimpleTestCase

from .workflow import STAGES, workflow_state


class JobCardWorkflowStateTests(SimpleTestCase):
    def job(self, status="New", progress=None):
        return SimpleNamespace(status=status, workflow_progress=progress or {})

    def test_new_job_starts_at_overview(self):
        state = workflow_state(self.job())
        self.assertEqual(state["current"], "overview")
        self.assertIn("inspection", state["locked"])

    def test_overview_approval_unlocks_inspection(self):
        state = workflow_state(self.job("Inspection", {"started": True, "completed": ["overview"]}))
        self.assertEqual(state["current"], "inspection")
        self.assertIn("overview", state["completed"])

    def test_inspection_done_unlocks_estimate(self):
        state = workflow_state(self.job("Estimate Pending"))
        self.assertEqual(state["current"], "estimate")
        self.assertEqual(state["completed"][:2], ["overview", "inspection"])

    def test_existing_work_is_not_sent_back_to_overview(self):
        state = workflow_state(self.job("In Progress"))
        self.assertEqual(state["current"], "work")
        self.assertEqual(state["completed"], ["overview", "inspection", "estimate"])

    def test_qc_and_invoice_follow_status(self):
        self.assertEqual(workflow_state(self.job("QC"))["current"], "qc")
        self.assertEqual(workflow_state(self.job("Ready for Delivery"))["current"], "invoice")

    def test_delivered_job_is_finished(self):
        state = workflow_state(self.job("Delivered"))
        self.assertTrue(state["finished"])
        self.assertEqual(state["current"], STAGES[-1])
        self.assertEqual(state["locked"], [])

    def test_completed_stages_are_idempotent(self):
        state = workflow_state(self.job("Inspection", {
            "started": True,
            "completed": ["overview", "overview"],
        }))
        self.assertEqual(state["completed"], ["overview"])
