from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import decorators, response, serializers, status
from rest_framework.exceptions import ValidationError
from apps.accounts.permissions import user_has_permission
from apps.employees.models import Employee
from common.viewsets import CompanyScopedModelViewSet
from .models import EmployeeWorkLog, JobCardEmployeeAssignment, JobWorkSession


def valid_amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError({"labourCharge": "Enter a valid amount."})
    if not amount.is_finite() or amount < 0 or amount > Decimal("9999999999.99"):
        raise ValidationError({"labourCharge": "Invalid labour charge."})
    return amount.quantize(Decimal("0.01"))


def event_entry(user, action, detail=None):
    return {
        "action": action, "at": timezone.now().isoformat(),
        "by": str(user.pk), "detail": detail or {},
    }


class JobWorkSessionSerializer(serializers.ModelSerializer):
    staffId = serializers.UUIDField(source="employee_id", read_only=True)
    staffName = serializers.CharField(source="employee.name", read_only=True)
    jobNumber = serializers.CharField(source="job.job_number", read_only=True)
    serviceName = serializers.CharField(source="service_name", read_only=True)
    labourCharge = serializers.DecimalField(
        source="labour_charge", max_digits=12, decimal_places=2, read_only=True
    )
    startedAt = serializers.DateTimeField(source="started_at", read_only=True)
    resumedAt = serializers.DateTimeField(source="resumed_at", read_only=True)
    completedAt = serializers.DateTimeField(source="completed_at", read_only=True)
    elapsedSeconds = serializers.IntegerField(source="elapsed_seconds", read_only=True)
    approvedMinutes = serializers.IntegerField(source="approved_minutes", read_only=True)
    correctionReason = serializers.CharField(source="correction_reason", read_only=True)

    class Meta:
        model = JobWorkSession
        exclude = (
            "company", "branch", "employee", "job", "service_name",
            "labour_charge", "started_at", "resumed_at", "completed_at",
            "elapsed_seconds", "approved_minutes", "correction_reason",
        )
        read_only_fields = [field.name for field in JobWorkSession._meta.fields]


class JobWorkSessionViewSet(CompanyScopedModelViewSet):
    queryset = JobWorkSession.objects.select_related(
        "job", "employee", "assignment", "reviewed_by"
    ).all()
    serializer_class = JobWorkSessionSerializer
    permission_prefix = "jobs"
    http_method_names = ["get", "post", "head", "options"]
    action_permission_map = {
        "start": "jobs.edit", "pause": "jobs.edit",
        "resume": "jobs.edit", "complete": "jobs.edit",
        "correct": "payroll.edit", "approve": "payroll.edit",
        "reject": "payroll.edit",
    }

    def create(self, request, *args, **kwargs):
        return response.Response({"message": "Use /work-sessions/start"}, status=405)

    def get_queryset(self):
        qs = super().get_queryset()
        for param, field in (("job", "job_id"), ("employee", "employee_id")):
            value = self.request.query_params.get(param)
            if value and value not in ("All", "all"):
                qs = qs.filter(**{field: value})
        return qs

    def _record(self, obj, action, user, detail=None):
        obj.history = [*(obj.history or []), event_entry(user, action, detail)]

    def _freeze(self, obj):
        if obj.resumed_at:
            now = timezone.now()
            obj.elapsed_seconds += max(0, int((now - obj.resumed_at).total_seconds()))
            obj.resumed_at = None

    @decorators.action(detail=False, methods=["post"])
    @transaction.atomic
    def start(self, request):
        assignment_id = request.data.get("assignment")
        service_name = str(request.data.get("serviceName") or "").strip()
        if not service_name or len(service_name) > 160:
            raise ValidationError({"serviceName": "Select a service (maximum 160 characters)."})
        assignment = JobCardEmployeeAssignment.objects.select_related(
            "job", "employee"
        ).filter(pk=assignment_id, company=request.user.company).first()
        if not assignment:
            raise ValidationError({"assignment": "Select a valid mechanic assignment."})
        if assignment.job.status != "In Progress":
            raise ValidationError({"job": "Start work only when the Job Card is In Progress."})
        Employee.objects.select_for_update().get(pk=assignment.employee_id)
        if JobWorkSession.objects.filter(
            company=assignment.company, employee=assignment.employee, status=JobWorkSession.RUNNING
        ).exists():
            raise ValidationError({"timer": "Mechanic already has a running timer. Pause or complete it first."})
        now = timezone.now()
        session = JobWorkSession.objects.create(
            company=assignment.company, branch=assignment.branch,
            job=assignment.job, employee=assignment.employee, assignment=assignment,
            service_name=service_name,
            labour_charge=valid_amount(request.data.get("labourCharge", 0)),
            started_at=now, resumed_at=now,
            history=[event_entry(request.user, "Started")],
        )
        return response.Response(self.get_serializer(session).data, status=201)

    @decorators.action(detail=True, methods=["post"])
    @transaction.atomic
    def pause(self, request, pk=None):
        obj = self.get_object()
        obj = JobWorkSession.objects.select_for_update().get(pk=obj.pk)
        if obj.status != JobWorkSession.RUNNING:
            raise ValidationError({"timer": "Only a running timer can be paused."})
        self._freeze(obj)
        obj.status = JobWorkSession.PAUSED
        self._record(obj, "Paused", request.user)
        obj.save()
        return response.Response(self.get_serializer(obj).data)

    @decorators.action(detail=True, methods=["post"])
    @transaction.atomic
    def resume(self, request, pk=None):
        obj = self.get_object()
        Employee.objects.select_for_update().get(pk=obj.employee_id)
        obj = JobWorkSession.objects.select_for_update().get(pk=obj.pk)
        if obj.status != JobWorkSession.PAUSED:
            raise ValidationError({"timer": "Only paused timers can resume."})
        if obj.job.status != "In Progress":
            raise ValidationError({"job": "Job Card must be In Progress to resume."})
        if JobWorkSession.objects.filter(
            company=obj.company, employee=obj.employee, status=JobWorkSession.RUNNING
        ).exclude(pk=obj.pk).exists():
            raise ValidationError({"timer": "Mechanic already has a running timer."})
        obj.status = JobWorkSession.RUNNING
        obj.resumed_at = timezone.now()
        self._record(obj, "Resumed", request.user)
        obj.save()
        return response.Response(self.get_serializer(obj).data)

    @decorators.action(detail=True, methods=["post"])
    @transaction.atomic
    def complete(self, request, pk=None):
        obj = self.get_object()
        obj = JobWorkSession.objects.select_for_update().get(pk=obj.pk)
        if obj.status not in (JobWorkSession.RUNNING, JobWorkSession.PAUSED):
            raise ValidationError({"timer": "Only running or paused work can be completed."})
        self._freeze(obj)
        if obj.elapsed_seconds < 1:
            raise ValidationError({"timer": "Work has no elapsed time yet."})
        obj.completed_at = timezone.now()
        obj.status = JobWorkSession.PENDING
        self._record(obj, "Submitted for review", request.user)
        obj.save()
        return response.Response(self.get_serializer(obj).data)


    @decorators.action(detail=True, methods=["post"])
    @transaction.atomic
    def correct(self, request, pk=None):
        obj = self.get_object()
        obj = JobWorkSession.objects.select_for_update().get(pk=obj.pk)
        if obj.status not in (JobWorkSession.PENDING, JobWorkSession.REJECTED):
            raise ValidationError({"timer": "Only unapproved work may be corrected."})
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError({"reason": "Supervisor correction reason is required."})
        try:
            minutes = int(request.data.get("minutes"))
        except (TypeError, ValueError):
            raise ValidationError({"minutes": "Enter corrected payable work minutes."})
        if minutes < 1 or minutes > 1440:
            raise ValidationError({"minutes": "Minutes must be between 1 and 1440."})
        if "labourCharge" in request.data:
            obj.labour_charge = valid_amount(request.data.get("labourCharge"))
        previous = obj.approved_minutes or max(1, (obj.elapsed_seconds + 59) // 60)
        obj.approved_minutes = minutes
        obj.correction_reason = reason
        obj.status = JobWorkSession.PENDING
        self._record(obj, "Corrected", request.user, {"fromMinutes": previous, "toMinutes": minutes, "reason": reason})
        obj.save()
        return response.Response(self.get_serializer(obj).data)

    @decorators.action(detail=True, methods=["post"])
    @transaction.atomic
    def reject(self, request, pk=None):
        obj = self.get_object()
        obj = JobWorkSession.objects.select_for_update().get(pk=obj.pk)
        if obj.status != JobWorkSession.PENDING:
            raise ValidationError({"timer": "Only pending work can be rejected."})
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError({"reason": "A rejection reason is required."})
        obj.status = JobWorkSession.REJECTED
        obj.reviewed_at = timezone.now()
        obj.reviewed_by = request.user
        self._record(obj, "Rejected", request.user, {"reason": reason})
        obj.save()
        return response.Response(self.get_serializer(obj).data)

    @decorators.action(detail=True, methods=["post"])
    @transaction.atomic
    def approve(self, request, pk=None):
        obj = self.get_object()
        obj = JobWorkSession.objects.select_for_update().get(pk=obj.pk)
        if obj.status == JobWorkSession.APPROVED:
            return response.Response(self.get_serializer(obj).data)
        if obj.status != JobWorkSession.PENDING:
            raise ValidationError({"timer": "Submit the completed work before approval."})
        if obj.employee.user_id == request.user.id and not request.user.is_superuser:
            raise ValidationError({"approval": "A mechanic cannot approve their own work."})

        # Validate service-wise revenue; it is not added again to the invoice.
        # A job with no labour quote may still approve hours with zero charge.
        approved_other = JobWorkSession.objects.filter(
            company=obj.company, job=obj.job, status=JobWorkSession.APPROVED
        ).exclude(pk=obj.pk).aggregate(value=Sum("labour_charge"))["value"] or Decimal("0")
        if obj.job.labour_total > 0 and approved_other + obj.labour_charge > obj.job.labour_total:
            raise ValidationError({
                "labourCharge": "Approved mechanic labour allocation exceeds the Job Card's labour charge."
            })
        minutes = obj.approved_minutes or max(1, (obj.elapsed_seconds + 59) // 60)
        log = obj.work_log
        if log is None:
            log = EmployeeWorkLog.objects.create(
                company=obj.company, branch=obj.branch,
                assignment=obj.assignment, employee=obj.employee, job=obj.job,
                work_date=timezone.localtime(obj.started_at).date(),
                minutes=minutes, labour_revenue=obj.labour_charge,
                service_revenue=obj.labour_charge, status="Approved",
                approved_by=request.user, approved_at=timezone.now(),
                notes=f"Verified timer: {obj.service_name} ({obj.id})",
            )
            obj.work_log = log
        else:
            log.minutes = minutes
            log.labour_revenue = obj.labour_charge
            log.service_revenue = obj.labour_charge
            log.status = "Approved"
            log.approved_by = request.user
            log.approved_at = timezone.now()
            log.save()

        totals = obj.assignment.work_logs.filter(status="Approved").aggregate(
            minutes=Sum("minutes"), labour=Sum("labour_revenue"),
            service=Sum("service_revenue"),
        )
        assignment = obj.assignment
        assignment.approved_work_minutes = totals["minutes"] or 0
        assignment.eligible_labour_revenue = totals["labour"] or Decimal("0")
        assignment.eligible_service_revenue = totals["service"] or Decimal("0")
        assignment.status = "Approved"
        assignment.save(update_fields=[
            "approved_work_minutes", "eligible_labour_revenue",
            "eligible_service_revenue", "status", "updated_at",
        ])
        obj.status = JobWorkSession.APPROVED
        obj.reviewed_at = timezone.now()
        obj.reviewed_by = request.user
        self._record(obj, "Approved", request.user, {"minutes": minutes})
        obj.save()
        return response.Response(self.get_serializer(obj).data)
