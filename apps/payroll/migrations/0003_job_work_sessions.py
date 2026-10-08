import uuid
from decimal import Decimal
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payroll", "0002_flexible_payroll_engine")]
    operations = [
        migrations.CreateModel(
            name="JobWorkSession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("service_name", models.CharField(max_length=160)),
                ("labour_charge", models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))),
                ("status", models.CharField(max_length=24, default="Running", choices=[("Running","Running"),("Paused","Paused"),("PendingApproval","Pending Approval"),("Approved","Approved"),("Rejected","Rejected")])),
                ("started_at", models.DateTimeField()),
                ("resumed_at", models.DateTimeField(null=True, blank=True)),
                ("completed_at", models.DateTimeField(null=True, blank=True)),
                ("elapsed_seconds", models.PositiveIntegerField(default=0)),
                ("approved_minutes", models.PositiveIntegerField(null=True, blank=True)),
                ("correction_reason", models.TextField(blank=True)),
                ("history", models.JSONField(default=list, blank=True)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("branch", models.ForeignKey(to="branches.branch", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True)),
                ("assignment", models.ForeignKey(to="payroll.jobcardemployeeassignment", on_delete=django.db.models.deletion.PROTECT, related_name="timed_sessions")),
                ("employee", models.ForeignKey(to="employees.employee", on_delete=django.db.models.deletion.PROTECT, related_name="timed_work_sessions")),
                ("job", models.ForeignKey(to="jobs.job", on_delete=django.db.models.deletion.PROTECT, related_name="timed_work_sessions")),
                ("reviewed_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True, related_name="reviewed_job_work_sessions")),
                ("work_log", models.OneToOneField(to="payroll.employeeworklog", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True, related_name="timed_session"))
            ],
            options={"ordering":["-started_at"]},
        ),
        migrations.AddConstraint(
            model_name="jobworksession",
            constraint=models.UniqueConstraint(
                fields=("company","employee"),condition=models.Q(status="Running"),
                name="unique_employee_running_job_timer",
            ),
        ),
        migrations.AddIndex(
            model_name="jobworksession",
            index=models.Index(fields=["employee","status"],name="job_timer_staff_status_idx"),
        ),
    ]
