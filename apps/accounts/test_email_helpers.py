"""Test-only fake Celery worker for legacy end-to-end onboarding assertions.

It stands in for broker + worker; production request paths never call SMTP.
"""
from unittest.mock import patch


def simulate_celery_worker(testcase):
    from apps.accounts.tasks import send_email_task

    def consume(task_name, *, args=None, **_kwargs):
        if task_name != send_email_task.name:
            raise AssertionError(f"Unexpected email task name: {task_name}")
        return send_email_task.run(*(args or []))

    mocked = patch("config.celery.app.send_task", side_effect=consume)
    mocked.start()
    testcase.addCleanup(mocked.stop)
