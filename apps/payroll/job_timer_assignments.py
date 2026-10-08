from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from apps.accounts.permissions import RolePermission
from .models import JobCardEmployeeAssignment


class JobTimerAssignmentsViewSet(viewsets.ViewSet):
    permission_classes = [RolePermission]
    permission_code = "jobs.view"

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
