from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import decorators, response, serializers, status
from rest_framework.exceptions import ValidationError

from apps.accounts.permissions import user_has_permission
from apps.expenses.models import Expense
from common.viewsets import CompanyScopedModelViewSet
from .models import Job, OutsideLabourCharge


class OutsideLabourSerializer(serializers.ModelSerializer):
    jobNumber = serializers.CharField(source="job.job_number", read_only=True)
    vehicleReg = serializers.CharField(source="job.vehicle.registration", read_only=True)
    workerName = serializers.CharField(source="worker_name", max_length=160)
    workerPhone = serializers.CharField(
        source="worker_phone", allow_blank=True, required=False, max_length=30
    )
    workDescription = serializers.CharField(source="work_description", max_length=300)
    customerCharge = serializers.DecimalField(
        source="customer_charge", max_digits=12, decimal_places=2,
        min_value=Decimal("0"), required=False
    )
    workerCharge = serializers.DecimalField(
        source="worker_charge", max_digits=12, decimal_places=2,
        min_value=Decimal("0.01")
    )
    paymentMethod = serializers.CharField(source="payment_method", read_only=True)
    paymentReference = serializers.CharField(source="payment_reference", read_only=True)
    paidAt = serializers.DateTimeField(source="paid_at", read_only=True)
    labourMargin = serializers.SerializerMethodField()

    class Meta:
        model = OutsideLabourCharge
        fields = (
            "id", "job", "jobNumber", "vehicleReg",
            "workerName", "workerPhone", "workDescription",
            "customerCharge", "workerCharge", "labourMargin",
            "status", "paymentMethod", "paymentReference", "paidAt",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "status", "created_at", "updated_at")

    def get_labourMargin(self, obj):
        return str(obj.customer_charge - obj.worker_charge)

    def validate_job(self, job):
        user = self.context["request"].user
        if not user.is_superuser and job.company_id != user.company_id:
            raise ValidationError("Job Card belongs to a different workshop.")
        if self.instance and job.pk != self.instance.job_id:
            raise ValidationError("Cannot move an outside labour entry to another Job Card.")
        return job

    def validate(self, attrs):
        if self.instance and self.instance.status == OutsideLabourCharge.STATUS_PAID:
            raise ValidationError("Paid outside labour is locked for audit history.")
        return attrs


class OutsideLabourViewSet(CompanyScopedModelViewSet):
    queryset = OutsideLabourCharge.objects.select_related("job", "job__vehicle", "expense")
    serializer_class = OutsideLabourSerializer
    permission_prefix = "expenses"
    action_permission_map = {
        "pay": "expenses.create",
        "summary": "expenses.view",
    }
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        job_id = self.request.query_params.get("job")
        if job_id:
            qs = qs.filter(job_id=job_id)
        record_status = self.request.query_params.get("status")
        if record_status:
            qs = qs.filter(status=record_status)
        return qs

    def perform_create(self, serializer):
        job = serializer.validated_data["job"]
        user = self.request.user
        serializer.save(company=job.company, branch=job.branch, created_by=user)

    def perform_update(self, serializer):
        if serializer.instance.status == OutsideLabourCharge.STATUS_PAID:
            raise ValidationError({"status": "Paid labour cannot be edited."})
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status == OutsideLabourCharge.STATUS_PAID:
            raise ValidationError({"status": "Paid labour cannot be deleted."})
        instance.delete()

    @decorators.action(detail=True, methods=["post"])
    @transaction.atomic
    def pay(self, request, pk=None):
        record = self.get_object()
        record = OutsideLabourCharge.objects.select_for_update().select_related("job").get(pk=record.pk)
        if record.status == OutsideLabourCharge.STATUS_PAID:
            # Repeated requests cannot duplicate the expense.
            return response.Response(self.get_serializer(record).data)

        method = str(request.data.get("paymentMethod") or "").strip()
        if method not in {"Cash", "UPI", "Bank Transfer", "Cheque", "Other"}:
            raise ValidationError({"paymentMethod": "Select a valid payment method."})
        reference = str(request.data.get("paymentReference") or "").strip()
        if len(reference) > 120:
            raise ValidationError({"paymentReference": "Maximum 120 characters."})
        date_value = request.data.get("date")
        if date_value:
            try:
                payment_date = date.fromisoformat(date_value)
            except (ValueError, TypeError):
                raise ValidationError({"date": "Use YYYY-MM-DD."})
            if payment_date > timezone.localdate():
                raise ValidationError({"date": "Payment date cannot be in the future."})
        else:
            payment_date = timezone.localdate()

        exp = Expense.objects.create(
            company=record.company, branch=record.branch,
            created_by=request.user, date=payment_date,
            category="Outside Labour",
            vendor=record.worker_name,
            description=f"{record.job.job_number}: {record.work_description}",
            amount=record.worker_charge,
            tax=Decimal("0"),
            payment_method=method,
            reference=reference or f"OUT-{str(record.pk)[:8].upper()}",
            status="Paid",
        )
        record.expense = exp
        record.status = OutsideLabourCharge.STATUS_PAID
        record.paid_at = timezone.now()
        record.paid_by = request.user
        record.payment_method = method
        record.payment_reference = reference
        record.save(update_fields=[
            "expense", "status", "paid_at", "paid_by", "payment_method",
            "payment_reference", "updated_at",
        ])
        return response.Response(self.get_serializer(record).data)

    @decorators.action(detail=False, methods=["get"])
    def summary(self, request):
        rows = self.get_queryset()
        totals = rows.aggregate(
            customer=Sum("customer_charge"), cost=Sum("worker_charge")
        )
        pending = rows.filter(status=OutsideLabourCharge.STATUS_PENDING).aggregate(
            amount=Sum("worker_charge")
        )["amount"] or Decimal("0")
        paid = rows.filter(status=OutsideLabourCharge.STATUS_PAID).aggregate(
            amount=Sum("worker_charge")
        )["amount"] or Decimal("0")
        return response.Response({
            "customerCharge": str(totals["customer"] or Decimal("0")),
            "workerCharge": str(totals["cost"] or Decimal("0")),
            "labourMargin": str((totals["customer"] or Decimal("0")) - (totals["cost"] or Decimal("0"))),
            "pending": str(pending), "paid": str(paid),
        })
