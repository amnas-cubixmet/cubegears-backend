from decimal import Decimal

from django.db import models
from django.db.models import Q

from common.models import CompanyOwnedModel
from .models import EmployeeWorkLog, JobCardEmployeeAssignment


class JobWorkSession(CompanyOwnedModel):
    RUNNING = "Running"
    PAUSED = "Paused"
    PENDING = "PendingApproval"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    STATUS_CHOICES = [
        (RUNNING, "Running"), (PAUSED, "Paused"),
        (PENDING, "Pending Approval"), (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
    ]

    assignment = models.ForeignKey(
        JobCardEmployeeAssignment, on_delete=models.PROTECT, related_name="timed_sessions"
    )
    job = models.ForeignKey(
        "jobs.Job", on_delete=models.PROTECT, related_name="timed_work_sessions"
    )
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="timed_work_sessions"
    )
    service_name = models.CharField(max_length=160)
    labour_charge = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=RUNNING)
    started_at = models.DateTimeField()
    resumed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    elapsed_seconds = models.PositiveIntegerField(default=0)
    approved_minutes = models.PositiveIntegerField(null=True, blank=True)
    correction_reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_job_work_sessions"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    work_log = models.OneToOneField(
        EmployeeWorkLog, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="timed_session"
    )
    history = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "employee"],
                condition=Q(status="Running"),
                name="unique_employee_running_job_timer",
            ),
        ]
        indexes = [
            models.Index(fields=["employee", "status"], name="job_timer_staff_status_idx"),
        ]
