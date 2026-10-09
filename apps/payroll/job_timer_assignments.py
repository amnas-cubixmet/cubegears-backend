from rest_framework import viewsets, status
from rest_framework.decorators import action
from django.db import IntegrityError, transaction
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from apps.accounts.permissions import RolePermission
from apps.jobs.models import Job
from apps.employees.models import Employee
from .models import JobCardEmployeeAssignment


class JobTimerAssignmentsViewSet(viewsets.ViewSet):
    permission_classes = [RolePermission]
    permission_map = {"GET": "jobs.view", "POST": "jobs.edit", "DELETE": "jobs.edit"}

    def list(self, request):
        job_id = request.query_params.get("job")
        if not job_id:
            raise ValidationError({"job": "Job Card ID is required."})
        rows = JobCardEmployeeAssignment.objects.select_related("employee").filter(
            company=request.user.company, job_id=job_id,
        ).order_by("employee__name")
        return Response([
            {"id": str(item.id), "staffId": str(item.employee_id),
             "staffName": item.employee.name, "role": item.role}
            for item in rows
        ])

    @transaction.atomic
    def create(self, request):
        job = Job.objects.filter(company=request.user.company, pk=request.data.get("job")).first()
        employee = Employee.objects.filter(company=request.user.company, pk=request.data.get("employee")).first()
        if not job or not employee:
            raise ValidationError({"assignment": "Select a Job Card and staff member in your workshop."})
        if job.status in {"Cancelled", "Delivered"}:
            raise ValidationError({"job": "Cannot assign new work to a closed Job Card."})
        item, _ = JobCardEmployeeAssignment.objects.get_or_create(
            company=request.user.company, job=job, employee=employee,
            defaults={
                "branch": job.branch or employee.branch,
                "role": employee.designation or "Technician",
                "commission_allocation_percent": 0,
                "status": "Assigned",
                "metadata": {"source": "manual", "paymentMode": "fixed_work_charge"},
            },
        )
        return Response({
            "id": str(item.id), "staffId": str(item.employee_id),
            "staffName": item.employee.name, "role": item.role,
        }, status=status.HTTP_200_OK)

    @transaction.atomic
    def destroy(self, request, pk=None):
        assignment = JobCardEmployeeAssignment.objects.filter(
            company=request.user.company, pk=pk,
        ).first()
        if not assignment:
            raise ValidationError({"assignment": "Assignment not found."})
        if assignment.timed_sessions.exists() or assignment.work_logs.exists():
            return Response({
                "message": "Worker has work records. Keep assignment for audit history."
            }, status=status.HTTP_409_CONFLICT)
        assignment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
