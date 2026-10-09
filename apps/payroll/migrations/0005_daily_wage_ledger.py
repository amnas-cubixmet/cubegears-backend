import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("payroll", "0004_fixed_work_charges"),
        ("attendance", "0007_attendance_daily_wage_finalization"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="EmployeeDailyWageRate",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("branch", models.ForeignKey(to="branches.branch", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True)),
                ("employee", models.ForeignKey(to="employees.employee", on_delete=django.db.models.deletion.PROTECT, related_name="daily_wage_rates")),
                ("rate", models.DecimalField(max_digits=12, decimal_places=2)),
                ("effective_from", models.DateField()),
                ("reason", models.CharField(max_length=250)),
                ("approved_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL, null=True)),
            ],
            options={"ordering":["-effective_from"]},
        ),
        migrations.CreateModel(
            name="DailyWageEntry",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("branch", models.ForeignKey(to="branches.branch", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True)),
                ("employee", models.ForeignKey(to="employees.employee", on_delete=django.db.models.deletion.PROTECT, related_name="daily_wage_entries")),
                ("attendance", models.OneToOneField(to="attendance.attendancerecord", on_delete=django.db.models.deletion.PROTECT, related_name="daily_wage_entry")),
                ("work_date", models.DateField()),
                ("attendance_status", models.CharField(max_length=40)),
                ("applied_rate", models.DecimalField(max_digits=12, decimal_places=2)),
                ("base_amount", models.DecimalField(max_digits=12, decimal_places=2)),
                ("finalized_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL, null=True)),
            ],
            options={"ordering":["-work_date"]},
        ),
        migrations.CreateModel(
            name="DailyWageExtra",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("branch", models.ForeignKey(to="branches.branch", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True)),
                ("employee", models.ForeignKey(to="employees.employee", on_delete=django.db.models.deletion.PROTECT, related_name="daily_wage_extras")),
                ("work_date", models.DateField()),
                ("category", models.CharField(max_length=20, choices=[("OT","Overtime"),("OD","Extra Duty"),("BONUS","Special Work Bonus"),("JOB","Additional Job Payment"),("MANUAL","Manual Extra Earning"),("CUSTOM","Custom Earning")])),
                ("amount", models.DecimalField(max_digits=12, decimal_places=2)),
                ("reason", models.TextField()),
                ("status", models.CharField(max_length=15, choices=[("Pending","Pending"),("Approved","Approved"),("Rejected","Rejected")], default="Pending")),
                ("request_key", models.CharField(max_length=120)),
                ("created_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL, null=True, related_name="created_daily_wage_extras")),
                ("reviewed_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL, null=True, related_name="reviewed_daily_wage_extras")),
                ("reviewed_at", models.DateTimeField(null=True)),
            ],
        ),
        migrations.CreateModel(
            name="WageAdjustment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("branch", models.ForeignKey(to="branches.branch", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True)),
                ("employee", models.ForeignKey(to="employees.employee", on_delete=django.db.models.deletion.PROTECT, related_name="daily_wage_adjustments")),
                ("work_date", models.DateField()),
                ("amount", models.DecimalField(max_digits=12, decimal_places=2)),
                ("reason", models.TextField()),
                ("kind", models.CharField(max_length=32, default="Manual")),
                ("created_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL, null=True)),
                ("request_key", models.CharField(max_length=120)),
            ],
        ),
        migrations.CreateModel(
            name="WagePayment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("branch", models.ForeignKey(to="branches.branch", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True)),
                ("employee", models.ForeignKey(to="employees.employee", on_delete=django.db.models.deletion.PROTECT, related_name="daily_wage_payments")),
                ("amount", models.DecimalField(max_digits=12, decimal_places=2)),
                ("payment_date", models.DateField()),
                ("method", models.CharField(max_length=30)),
                ("reference", models.CharField(max_length=150, blank=True)),
                ("request_key", models.CharField(max_length=120)),
                ("created_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL, null=True)),
            ],
            options={"ordering":["-created_at"]},
        ),
        migrations.CreateModel(
            name="WagePaymentReversal",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("branch", models.ForeignKey(to="branches.branch", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True)),
                ("payment", models.OneToOneField(to="payroll.wagepayment", on_delete=django.db.models.deletion.PROTECT, related_name="reversal")),
                ("reason", models.TextField()),
                ("approved_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL, null=True)),
            ],
        ),
        migrations.CreateModel(
            name="WageAuditLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(to="companies.company", on_delete=django.db.models.deletion.CASCADE)),
                ("branch", models.ForeignKey(to="branches.branch", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True)),
                ("employee", models.ForeignKey(to="employees.employee", on_delete=django.db.models.deletion.PROTECT, related_name="daily_wage_audit")),
                ("actor", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL, null=True)),
                ("action", models.CharField(max_length=70)),
                ("work_date", models.DateField(null=True)),
                ("original", models.JSONField(default=dict)),
                ("updated", models.JSONField(default=dict)),
                ("reason", models.TextField(blank=True)),
            ],
            options={"ordering":["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="employeedailywagerate",
            constraint=models.UniqueConstraint(fields=["employee","effective_from"], name="unique_employee_daily_rate_date"),
        ),
        migrations.AddConstraint(
            model_name="dailywageentry",
            constraint=models.UniqueConstraint(fields=["employee","work_date"], name="unique_employee_daily_earning"),
        ),
        migrations.AddConstraint(
            model_name="dailywageextra",
            constraint=models.UniqueConstraint(fields=["employee","request_key"], name="unique_employee_wage_extra_key"),
        ),
        migrations.AddConstraint(
            model_name="wageadjustment",
            constraint=models.UniqueConstraint(fields=["employee","request_key"], name="unique_employee_wage_adjustment_key"),
        ),
        migrations.AddConstraint(
            model_name="wagepayment",
            constraint=models.UniqueConstraint(fields=["employee","request_key"], name="unique_employee_daily_wage_payment"),
        ),
        migrations.AddConstraint(
            model_name="employeedailywagerate",
            constraint=models.CheckConstraint(condition=Q(rate__gt=0), name="daily_rate_positive"),
        ),
        migrations.AddConstraint(
            model_name="dailywageextra",
            constraint=models.CheckConstraint(condition=Q(amount__gt=0), name="daily_wage_extra_positive"),
        ),
        migrations.AddConstraint(
            model_name="wagepayment",
            constraint=models.CheckConstraint(condition=Q(amount__gt=0), name="daily_wage_payment_positive"),
        ),
    ]
