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
