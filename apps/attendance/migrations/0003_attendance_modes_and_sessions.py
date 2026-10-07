import datetime
import django.db.models.deletion
import uuid
from django.db import migrations, models


def backfill_sessions(apps, schema_editor):
    AttendanceRecord = apps.get_model("attendance", "AttendanceRecord")
    AttendanceSession = apps.get_model("attendance", "AttendanceSession")

    for record in AttendanceRecord.objects.exclude(clock_in__isnull=True).iterator():
        AttendanceSession.objects.get_or_create(
            attendance_id=record.id,
            session_number=1,
            defaults={
                "id": uuid.uuid4(),
                "company_id": record.company_id,
                "branch_id": record.branch_id,
                "clock_in": record.clock_in,
                "clock_out": record.clock_out,
                "worked_minutes": record.worked_minutes or 0,
                "auto_closed": False,
                "source": "legacy",
                "clock_in_location": record.location or {},
                "clock_out_location": {},
                "note": "Backfilled from legacy attendance record",
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancerule",
            name="attendance_mode",
            field=models.CharField(
                choices=[
                    ("single", "Single check-in / check-out"),
                    ("multi", "Multiple check-in / check-out sessions"),
                    ("auto_checkout", "Check-in with automatic checkout"),
                    ("hybrid", "Manual checkout with automatic fallback"),
                ],
                default="single",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="shift_start_time",
            field=models.TimeField(default=datetime.time(9, 0)),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="shift_end_time",
            field=models.TimeField(default=datetime.time(18, 0)),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="max_sessions_per_day",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="auto_checkout_grace_minutes",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="missing_punch_policy",
            field=models.CharField(default="request_correction", max_length=30),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="allow_self_approval",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="weekend_days",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="alternate_saturday_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="alternate_saturday_pattern",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="weekend_attendance_policy",
            field=models.CharField(default="weekly_off", max_length=40),
        ),
        migrations.AddField(
            model_name="attendancerule",
            name="weekend_effective_from",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="AttendanceSession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("session_number", models.PositiveIntegerField(default=1)),
                ("clock_in", models.DateTimeField()),
                ("clock_out", models.DateTimeField(blank=True, null=True)),
                ("worked_minutes", models.PositiveIntegerField(default=0)),
                ("auto_closed", models.BooleanField(default=False)),
                ("source", models.CharField(default="web", max_length=30)),
                ("clock_in_location", models.JSONField(blank=True, default=dict)),
                ("clock_out_location", models.JSONField(blank=True, default=dict)),
                ("note", models.CharField(blank=True, max_length=255)),
                (
                    "attendance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sessions",
                        to="attendance.attendancerecord",
                    ),
                ),
                (
                    "branch",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="branches.branch",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="companies.company",
                    ),
                ),
            ],
            options={
                "ordering": ["session_number"],
            },
        ),
        migrations.AddConstraint(
            model_name="attendancesession",
            constraint=models.UniqueConstraint(
                fields=("attendance", "session_number"),
                name="unique_attendance_session_number",
            ),
        ),
        migrations.AddConstraint(
            model_name="attendancesession",
            constraint=models.UniqueConstraint(
                fields=("attendance",),
                condition=models.Q(clock_out__isnull=True),
                name="unique_open_attendance_session",
            ),
        ),
        migrations.AddIndex(
            model_name="attendancesession",
            index=models.Index(fields=["clock_out"], name="att_session_open_idx"),
        ),
        migrations.RunPython(backfill_sessions, migrations.RunPython.noop),
    ]
