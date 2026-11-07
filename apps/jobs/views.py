from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4
import re
from django.conf import settings
import mimetypes
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import NotFound
from django.utils.dateparse import parse_datetime
from django.db import transaction
from django.utils import timezone
from rest_framework import decorators,response,status
from rest_framework.exceptions import ValidationError
from common.viewsets import CompanyScopedModelViewSet
from .models import Job,JobActivity,JobEstimate,JobPart,JobPhoto
from .audit import record_job_event
from .inspection_inputs import validate_finding, validate_diagnostic
from .estimate_requests import estimate_identity, saved_estimate_identity, attach_identity
from .serializers import JobSerializer,JobEstimateSerializer,JobPartSerializer,JobPhotoSerializer,JobActivitySerializer
from .workflow import STAGES, NEXT_STATUS, workflow_state, stage_index_from_status
from .complaint_status import sync_complaint_status, manual_complaint_status, unresolved_complaints, VALID_COMPLAINT_STATUSES
from .india_gst import gst_context, calculate_tax, gst_rate
from apps.invoices.gst_rate_options import find_gst_choice

def _same_quick_create_phone(stored, supplied):
    """Allow verified +91 normalization of legacy 10-digit Indian contacts only."""
    first = "".join(filter(str.isdigit, str(stored or "")))
    second = "".join(filter(str.isdigit, str(supplied or "")))
    if first == second:
        return True
    return (len(first) == 10 and second == "91" + first) or (
        len(second) == 10 and first == "91" + second
    )


FLOW=[Job.STATUS_NEW,Job.STATUS_INSPECTION,Job.STATUS_ESTIMATE_PENDING,Job.STATUS_APPROVED,Job.STATUS_IN_PROGRESS,Job.STATUS_WAITING_PARTS,Job.STATUS_QC,Job.STATUS_READY,Job.STATUS_DELIVERED]

class JobViewSet(CompanyScopedModelViewSet):
    queryset=Job.objects.select_related("customer","vehicle","advisor","technician").prefetch_related("parts","photos","activities","estimates")
    serializer_class=JobSerializer
    action_permission_map = {
        "status": "jobs.edit",
        "workflow": {"GET": "jobs.view", "POST": "jobs.edit"},
        "inspection_detail": "jobs.view",
        "inspection_start": "jobs.edit",
        "inspection_checklist": "jobs.edit",
        "inspection_findings": "jobs.edit",
        "inspection_finding_detail": "jobs.edit",
        "inspection_diagnostic": "jobs.edit",
        "inspection_photo": "jobs.edit",
        "inspection_complete": "jobs.edit",
        "inspection_skip": "jobs.edit",
        "work_skip": "jobs.edit",
        "work_service_add": "jobs.edit",
        "complaint_status": "jobs.edit",
        "inspection_add_to_estimate": "jobs.edit",
        "estimates": {"GET": "jobs.view", "POST": "jobs.edit"},
        "quick_estimate": "jobs.edit",
        "quick_estimate_detail": "jobs.edit",
        "estimate_decision": "jobs.edit",
        "estimate_skip": "jobs.edit",
        "photos": {"GET": "jobs.view", "POST": "jobs.edit"},
        "upload_work_photo": "jobs.edit",
        "work_photo_file": "jobs.view",
        "activity": "jobs.view",
        "issue_part": ["jobs.edit", "stock.edit"],
        "cost_summary": ["jobs.view", "payroll.view", "expenses.view", "stock.view"],
    }
    def get_queryset(self):
        qs=super().get_queryset(); st=self.request.query_params.get("status")
        return qs.filter(status=st) if st else qs
    @decorators.action(detail=True, methods=["get"], url_path="cost-summary")
    def cost_summary(self, request, pk=None):
        """Recorded direct job costs, never customer charges or estimated selling prices.

        Daily-wage overhead is not allocated to individual jobs. This endpoint
        therefore explicitly indicates if wages are not included in its total.
        """
        from apps.inventory.models import StockMovement
        from apps.payroll.models import JobWorkSession
        from .models import OutsideLabourCharge

        job = self.get_object()  # Company-scoped + permission-checked.
        internal = JobWorkSession.objects.filter(
            company=job.company, job=job, status=JobWorkSession.APPROVED
        )
        mechanic_cost = sum((row.worker_charge for row in internal), Decimal("0"))
        unallocated_mechanic = internal.filter(worker_charge=0).exists()
        pending_mechanic = JobWorkSession.objects.filter(
            company=job.company, job=job,
        ).exclude(status=JobWorkSession.APPROVED).exists()

        contractors = OutsideLabourCharge.objects.filter(company=job.company, job=job)
        outside_cost = sum((row.worker_charge for row in contractors), Decimal("0"))
        outside_pending = sum(
            (row.worker_charge for row in contractors if row.status == OutsideLabourCharge.STATUS_PENDING),
            Decimal("0"),
        )

        # Issue quantities are negative, returns positive. Older movements did
        # not snapshot unit_cost; for those only, use current item cost_price.
        movements = StockMovement.objects.select_related("item").filter(
            company=job.company, job=job,
            movement_type__in=("job_issue", "return"),
        )
        part_cost = Decimal("0")
        used_current_cost = False
        for movement in movements:
            price = movement.unit_cost
            if price <= 0:
                price = movement.item.cost_price
                used_current_cost = True
            part_cost -= movement.quantity * price
        part_cost = max(Decimal("0"), part_cost)
        # Direct purchases are used on the job, not added to StockMovement.
        # Include them once from the job's persisted outsideParts ledger.
        outside_parts = (job.parts_workflow or {}).get("outsideParts") or []
        # Pending, approved and rejected quotations are NOT spent money.
        # Missing status marks a historical direct purchase.
        outside_parts_cost = sum(
            (Decimal(str(part.get("totalCost") or "0"))
             for part in outside_parts if part.get("status", "Purchased") == "Purchased"),
            Decimal("0"),
        )
        part_cost += outside_parts_cost

        def amount(number):
            return str(number.quantize(Decimal("0.01")))

        return response.Response({
            "mechanicCost": amount(mechanic_cost),
            "outsideStaffCost": amount(outside_cost),
            "partsCost": amount(part_cost),
            "outsidePartsCost": amount(outside_parts_cost),
            "totalCost": amount(mechanic_cost + outside_cost + part_cost),
            "outsidePending": amount(outside_pending),
            "unallocatedDailyWages": unallocated_mechanic,
            "unapprovedMechanicWork": pending_mechanic,
            "partsAtCurrentCost": used_current_cost,
            "note": "Total is recorded direct cost, excludes unallocated daily wages and workshop overhead.",
        })

    def _guard_active_timers(self,job,next_status):
        if next_status in {"QC","Ready for Delivery","Delivered"}:
            from apps.payroll.models import JobWorkSession
            if JobWorkSession.objects.filter(
                company=job.company,job=job,
                status__in=[JobWorkSession.RUNNING,JobWorkSession.PAUSED],
            ).exists():
                raise ValidationError({
                    "status":"Pause and complete all active mechanic timers before QC or delivery."
                })

    def _inspection_skip_block_reason(self, job):
        """Return a policy/stage/safety reason, or None for a permitted skip."""
        data = job.inspection or {}
        if data.get("status") == "Skipped":
            return "Inspection was already skipped. A second skip is not permitted."
        if data.get("status") == "Completed":
            return "Completed inspections cannot be skipped."
        if workflow_state(job)["current"] != "inspection":
            return "Complete Overview first, or return to the active Inspection stage."
        if job.company.require_vehicle_inspection:
            return "Workshop policy requires an inspection for every vehicle."
        if data.get("mandatorySafetyInspection") or data.get("requiresSafetyInspection"):
            return "A mandatory safety inspection is required for this Job Card."
        if "Critical" in (data.get("checklist") or {}).values() or any(
            str(finding.get("severity") or "").lower() == "critical"
            for finding in (data.get("findings") or []) if isinstance(finding, dict)
        ):
            return "Critical safety findings require inspection; skipping is disabled."
        return None

    def _assert_inspection_editable(self, job):
        """Completed checks may be corrected before customer approves an estimate."""
        current = workflow_state(job)["current"]
        if (job.inspection or {}).get("status") == "Skipped":
            raise ValidationError({"inspection": "Skipped inspection is read-only. Existing findings and photos are preserved."})
        completed = (job.inspection or {}).get("status") == "Completed"
        # The quick-create form can save initial inspection details in Overview.
        # Completed inspections are strictly editable only at Estimate stage.
        allowed = {"estimate"} if completed else {"overview", "inspection", "estimate"}
        if current not in allowed or job.estimates.filter(status="Approved").exists():
            raise ValidationError({"inspection": "Inspection changes are locked after estimate approval."})
        if job.status == Job.STATUS_DELIVERED:
            raise ValidationError({"inspection": "Delivered Job Cards cannot be edited."})

    def _audit_inspection_change(self, job, label, before, after):
        if (job.inspection or {}).get("status") == "Completed":
            updated = dict(job.inspection or {})
            updated["revisedAt"] = timezone.now().isoformat()
            job.inspection = updated
            job.save(update_fields=["inspection", "updated_at"])
            JobActivity.objects.create(
                company=job.company, branch=job.branch, job=job,
                actor=self.request.user, event="Inspection Updated",
                description=label, metadata={"before": before, "after": after},
            )

    @transaction.atomic
    def perform_update(self, serializer):
        scoped = self.get_object()
        job = Job.objects.select_for_update().get(
            pk=scoped.pk, company_id=self.request.user.company_id,
        )
        serializer.instance = job
        if job.status == Job.STATUS_DELIVERED and any(
            field not in {"customer_rating", "customer_feedback"}
            for field in serializer.validated_data
        ):
            raise ValidationError({
                "job": "Delivered Job Cards are locked. Customer feedback may still be recorded.",
            })
        tracked = {
            key: getattr(job, key)
            for key in ("complaints", "work_updates", "qc", "work", "technician_id", "priority")
            if key in serializer.validated_data or
            (key == "technician_id" and "technician" in serializer.validated_data)
        }
        previous_status = job.status
        next_status = serializer.validated_data.get("status")
        if "inspection" in serializer.validated_data:
            self._assert_inspection_editable(job)
            proposed = serializer.validated_data["inspection"]
            if not isinstance(proposed, dict):
                raise ValidationError({"inspection": "Inspection must be an object."})
            if proposed.get("status") in {"Completed", "Skipped"}:
                raise ValidationError({"inspection": "Use the dedicated Complete Inspection or Skip Inspection action."})
            if (job.inspection or {}).get("status") == "Completed":
                raise ValidationError({
                    "inspection": "Use inspection edit actions to preserve revision history."
                })
        if next_status:
            unlocked = workflow_state(job)["current"]
            if stage_index_from_status(next_status) > STAGES.index(unlocked):
                raise ValidationError({"status": "Complete the current Job Card stage first."})
            if stage_index_from_status(next_status) < stage_index_from_status(job.status):
                raise ValidationError({
                    "status": "Completed workflow stages cannot be reopened through a generic Job Card edit.",
                })
        if "qc" in serializer.validated_data:
            # The client can edit checklist/notes, but the inspector's identity
            # must always come from the authenticated user actually saving QC.
            qc_data = serializer.validated_data["qc"]
            if not isinstance(qc_data, dict):
                raise ValidationError({"qc": "Quality Check must be an object."})
            inspector_name = str(getattr(self.request.user, "name", "") or "").strip()
            if not inspector_name:
                raise ValidationError({"qc": "Your login account needs a name to save QC."})
            serializer.validated_data["qc"] = {
                **qc_data,
                "inspector": inspector_name,
                "inspectorUserId": str(self.request.user.pk),
            }
        technician = serializer.validated_data.get("technician")
        if technician and job.branch_id and technician.branch_id and technician.branch_id != job.branch_id:
            raise ValidationError({"technician": "Technician must belong to the Job Card branch."})
        self._guard_active_timers(job,next_status)
        updated = serializer.save()
        if "status" in serializer.validated_data or "complaints" in serializer.validated_data:
            if sync_complaint_status(updated):
                updated.save(update_fields=["complaints", "updated_at"])
        if previous_status != updated.status:
            record_job_event(updated, self.request.user, "Status Changed",
                             f"{previous_status} → {updated.status}")
        tracked_events = {
            "complaints": "Customer Complaints Updated",
            "work_updates": "Technician Updates Saved",
            "qc": "Quality Check Updated",
            "work": "Work Records Updated",
            "technician_id": "Mechanic Assignment Changed",
            "priority": "Job Priority Changed",
        }
        for key, before in tracked.items():
            after = getattr(updated, key)
            if before != after:
                label = tracked_events[key]
                metadata = {"section": key}
                if isinstance(after, list):
                    metadata["count"] = len(after)
                record_job_event(updated, self.request.user, label,
                                 f"{label} for {updated.job_number}", metadata)


    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Accept the quick job form while preserving the UUID-based canonical API."""
        from apps.customers.models import Customer
        from apps.vehicles.models import Vehicle
        from django.db.models import Value
        from django.db.models.functions import Replace, Upper

        data = request.data.copy()
        # Lock the real Company row for the whole creation transaction. A
        # repeated request key returns the prior Job Card BEFORE attempting to
        # create another Customer/Vehicle or consuming a new job number.
        from apps.companies.models import Company
        Company.objects.select_for_update().get(pk=request.user.company_id)
        raw_request_id = data.get("clientRequestId")
        self._job_create_request_id = ""
        if raw_request_id not in (None, ""):
            if not isinstance(raw_request_id, str) or not re.fullmatch(
                r"[a-zA-Z0-9:_-]{12,120}", raw_request_id,
            ):
                raise ValidationError({
                    "clientRequestId": "Use a unique 12–120 character request ID.",
                })
            self._job_create_request_id = raw_request_id
            previous_job = Job.objects.filter(
                company_id=request.user.company_id,
                workflow_progress__createRequestId=raw_request_id,
            ).first()
            if previous_job is not None:
                return response.Response(
                    self.get_serializer(previous_job).data, status=status.HTTP_200_OK,
                )

        vehicle_value = data.get("vehicle")
        # Also support a preselected existing customer with a typed/new vehicle.
        # A canonical customer+vehicle UUID payload without a registration still
        # passes directly to the regular serializer, as before.
        quick_create = bool(data.get("vehicleReg")) or isinstance(vehicle_value, dict)

        if quick_create:
            name = str(data.get("customerName") or "").strip()
            phone = str(data.get("customerPhone") or "").strip()
            registration = str(
                data.get("vehicleReg") or (vehicle_value or {}).get("registration", "")
            ).strip().upper()
            normalized_reg = "".join(registration.split())
            if not name or not phone or not normalized_reg:
                raise ValidationError({
                    "customerName": "Customer name is required." if not name else [],
                    "customerPhone": "Customer phone is required." if not phone else [],
                    "vehicleReg": "Registration is required." if not normalized_reg else [],
                })

            company = request.user.company
            branch = request.user.branch
            requested_branch = data.get("branchId")
            if requested_branch:
                from apps.branches.models import Branch
                from apps.accounts.permissions import user_has_permission
                resolved_branch = Branch.objects.filter(
                    pk=requested_branch, company=company, is_active=True,
                ).first()
                if not resolved_branch:
                    raise ValidationError({"branchId": "Select an active workshop branch."})
                if (not branch or branch.pk != resolved_branch.pk) and not (
                    user_has_permission(request.user, "company.manage")
                    or user_has_permission(request.user, "branches.manage")
                ):
                    raise ValidationError({"branchId": "You cannot create Job Cards in a different branch."})
                branch = resolved_branch
            self._job_create_branch = branch
            chosen_customer = None
            chosen_id = data.get("customer")
            if chosen_id:
                chosen_customer = Customer.objects.filter(
                    company=company, pk=chosen_id, status="active"
                ).first()
                if chosen_customer is None:
                    raise ValidationError({"customer": "Select a valid active customer."})

            # Preserve separate Make / Model / Fuel on new Job Cards. The old
            # combined vehicleInfo payload remains supported for older clients.
            legacy_parts = str(data.get("vehicleInfo") or "").strip().split(maxsplit=1)
            make = str(data.get("vehicleMake") or (legacy_parts[0] if legacy_parts else "")).strip()
            model = str(data.get("vehicleModel") or (legacy_parts[1] if len(legacy_parts) > 1 else "")).strip()
            fuel_type = str(data.get("fuelType") or "").strip()
            if len(make) > 80 or len(model) > 80 or len(fuel_type) > 30:
                raise ValidationError({"vehicleInfo": "Vehicle make, model or fuel type is too long."})
            if fuel_type and fuel_type not in {
                "Petrol", "Diesel", "CNG", "LPG", "Electric", "Hybrid", "Other",
            }:
                raise ValidationError({"fuelType": "Select a supported vehicle fuel type."})
            raw_km = data.get("kilometre")
            km = None
            if raw_km not in (None, ""):
                try:
                    km = int(str(raw_km))
                except (TypeError, ValueError):
                    raise ValidationError({"kilometre": "Enter a valid non-negative odometer reading."})
                if km < 0 or km > 999999999:
                    raise ValidationError({"kilometre": "Enter a valid non-negative odometer reading."})
                data["odometer"] = km

            vehicle = (
                Vehicle.objects.filter(company=company)
                .annotate(reg_key=Upper(Replace("registration", Value(" "), Value(""))))
                .filter(reg_key=normalized_reg)
                .select_related("customer")
                .first()
            )
            if vehicle:
                customer = vehicle.customer
                if chosen_customer and vehicle.customer_id != chosen_customer.id:
                    raise ValidationError({
                        "vehicleReg": "This vehicle belongs to another customer. Verify ownership before continuing."
                    })
                if not chosen_customer and not _same_quick_create_phone(customer.phone, phone):
                    raise ValidationError({
                        "vehicleReg": "This registration belongs to another customer. Open the vehicle record to verify ownership."
                    })
                if isinstance(vehicle_value, str) and vehicle_value and str(vehicle.id) != vehicle_value:
                    raise ValidationError({"vehicle": "Selected vehicle does not match registration."})
                # Existing vehicle metadata is not overwritten just because
                # an operator creates a Job Card. Explicit updates require
                # existing Vehicle Edit permission and same-tenant ownership.
                if data.get("vehicleDetailsUpdate") is True:
                    from apps.accounts.permissions import user_has_permission
                    if not user_has_permission(request.user, "vehicles.edit"):
                        raise ValidationError({"vehicle": "Vehicle Edit permission is required to update saved vehicle details."})
                    changes = {}
                    if make:
                        changes["make"] = make
                    if model:
                        changes["model"] = model
                    if fuel_type:
                        changes["fuel_type"] = fuel_type
                    if km is not None:
                        changes["odometer"] = km
                    if changes:
                        for key, value in changes.items():
                            setattr(vehicle, key, value)
                        vehicle.save(update_fields=[*changes.keys(), "updated_at"])
            else:
                if isinstance(vehicle_value, str) and vehicle_value:
                    raise ValidationError({"vehicle": "Selected vehicle does not match registration."})
                customer = chosen_customer or Customer.objects.filter(
                    company=company, phone=phone
                ).first()
                if customer is None:
                    customer = Customer.objects.create(
                        company=company, branch=branch, name=name, phone=phone,
                        email=str(data.get("customerEmail") or "").strip(),
                        address=str(data.get("customerAddress") or "").strip(),
                    )
                vehicle = Vehicle.objects.create(
                    company=company, branch=branch, customer=customer,
                    registration=registration, make=make, model=model,
                    fuel_type=fuel_type, odometer=km or 0,
                    vin=str(data.get("vin") or "").strip(),
                )

            if chosen_customer and (
                chosen_customer.name != name or
                not _same_quick_create_phone(chosen_customer.phone, phone)
            ):
                raise ValidationError({
                    "customer": "Selected customer details changed. Select the customer again."
                })
            data["customer"] = str(customer.id)
            data["vehicle"] = str(vehicle.id)
            if "fuelLevel" in data:
                data["fuel_level"] = data["fuelLevel"]

            # Existing Employee IDs are not User IDs. Resolve both within the
            # workshop, preserving the current payroll assignment flow.
            from apps.employees.models import Employee
            for input_key, target_key in (
                ("assignedEmployeeId", "technician"),
                ("serviceAdvisorEmployeeId", "advisor"),
            ):
                employee_id = data.get(input_key)
                if not employee_id:
                    continue
                employee = Employee.objects.filter(
                    pk=employee_id, company=company, status="Active",
                ).select_related("user").first()
                if not employee:
                    raise ValidationError({input_key: "Choose an active staff member from your workshop."})
                if branch and employee.branch_id and employee.branch_id != branch.pk:
                    raise ValidationError({
                        input_key: "Selected staff belongs to a different workshop branch.",
                    })
                if employee.user_id and employee.user.is_active:
                    data[target_key] = str(employee.user_id)

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return response.Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self,serializer):
        company=self.request.user.company
        customer=serializer.validated_data.get("customer")
        vehicle=serializer.validated_data.get("vehicle")
        technician=serializer.validated_data.get("technician")
        if customer and customer.company_id != company.id:
            raise ValidationError({"customer":"Customer belongs to another company."})
        if vehicle and vehicle.company_id != company.id:
            raise ValidationError({"vehicle":"Vehicle belongs to another company."})
        if vehicle and customer and vehicle.customer_id != customer.id:
            raise ValidationError({"vehicle":"Vehicle does not belong to the selected customer."})
        if technician and technician.company_id != company.id:
            raise ValidationError({"technician":"Technician belongs to another company."})
        target_branch = getattr(self, "_job_create_branch", self.request.user.branch)
        if technician and target_branch and technician.branch_id and technician.branch_id != target_branch.pk:
            raise ValidationError({"technician": "Technician must belong to the Job Card branch."})
        advisor=serializer.validated_data.get("advisor") or self.request.user
        if advisor.company_id != company.id:
            raise ValidationError({"advisor":"Service advisor belongs to another company."})
        # The Company row is locked by create(); generated numbers never
        # silently reuse an existing number after an earlier Job was deleted.
        selected_branch = getattr(self, "_job_create_branch", self.request.user.branch)
        if selected_branch and (
            selected_branch.company_id != company.pk or not selected_branch.is_active
        ):
            raise ValidationError({"branchId": "Select an active workshop branch."})
        number = str(serializer.validated_data.get("job_number") or "").strip()
        if number:
            if len(number) > 40 or Job.objects.filter(
                company=company, job_number=number,
            ).exists():
                raise ValidationError({"jobNumber": "Job Card number is invalid or already in use."})
        else:
            sequence = Job.objects.filter(company=company).count() + 1
            while True:
                number = f"JOB-{timezone.now().year}-{sequence:05d}"
                if not Job.objects.filter(company=company, job_number=number).exists():
                    break
                sequence += 1
        progress = {}
        if getattr(self, "_job_create_request_id", ""):
            progress["createRequestId"] = self._job_create_request_id
        job = serializer.save(
            company=company, branch=selected_branch, job_number=number,
            advisor=advisor, workflow_progress=progress,
        )
        JobActivity.objects.create(
            company=company, branch=job.branch, job=job,
            event="Job Created", description=f"{number} created",
            actor=self.request.user, to_status=job.status,
        )

    @decorators.action(detail=True, methods=["patch"])
    @transaction.atomic
    def status(self, request, pk=None):
        scoped = self.get_object()
        job = Job.objects.select_for_update().get(
            pk=scoped.pk, company_id=request.user.company_id,
        )
        new_status = request.data.get("status")
        if new_status not in FLOW:
            raise ValidationError({"status": "Invalid Job Card status."})
        if new_status == job.status:
            return response.Response(self.get_serializer(job).data)
        if stage_index_from_status(new_status) < stage_index_from_status(job.status):
            raise ValidationError({
                "status": "A completed Job Card stage cannot be reopened by changing its status.",
            })
        if stage_index_from_status(new_status) > STAGES.index(workflow_state(job)["current"]):
            raise ValidationError({"status": "Complete the active Job Card stage first."})
        old_status = job.status
        self._guard_active_timers(job, new_status)
        if new_status == Job.STATUS_DELIVERED:
            job.delivered_at = timezone.now()
        job.status = new_status
        fields = ["status", "delivered_at", "updated_at"]
        if sync_complaint_status(job):
            fields.append("complaints")
        job.save(update_fields=fields)
        JobActivity.objects.create(
            company=job.company, branch=job.branch, job=job,
            event="Status Changed", description=f"{old_status} → {new_status}",
            actor=request.user, from_status=old_status, to_status=new_status,
        )
        return response.Response(self.get_serializer(job).data)

    @decorators.action(
        detail=True, methods=["post"],
        url_path=r"complaints/(?P<complaint_id>[^/.]+)/status",
    )
    @transaction.atomic
    def complaint_status(self, request, pk=None, complaint_id=None):
        """Manual, audited complaint status update; never auto-resolve on QC."""
        scoped = self.get_object()
        job = Job.objects.select_for_update().get(pk=scoped.pk, company=scoped.company)
        if job.status == Job.STATUS_DELIVERED:
            raise ValidationError({"complaint": "Delivered Job Cards are read-only."})
        desired = request.data.get("status")
        if not isinstance(desired, str) or desired not in VALID_COMPLAINT_STATUSES:
            raise ValidationError({"status": "Choose Pending, In Progress, Resolved, Not Resolved or Not Applicable."})
        notes = request.data.get("notes", "")
        work_reference = request.data.get("workReference", "")
        if not isinstance(notes, str) or len(notes.strip()) > 1000:
            raise ValidationError({"notes": "Notes must be 1000 characters or fewer."})
        if not isinstance(work_reference, str) or len(work_reference.strip()) > 120:
            raise ValidationError({"workReference": "Work reference must be 120 characters or fewer."})
        notes, work_reference = notes.strip(), work_reference.strip()
        if desired in {"Not Applicable", "Not Resolved"} and len(notes) < 5:
            raise ValidationError({"notes": "Record a reason (at least 5 characters)."})
        existing = list(job.complaints or [])
        match = next((i for i, item in enumerate(existing)
                      if isinstance(item, dict) and str(item.get("id")) == complaint_id), None)
        if match is None and complaint_id.startswith("row-"):
            try:
                index = int(complaint_id[4:])
            except ValueError:
                index = -1
            if 0 <= index < len(existing) and isinstance(existing[index], dict) and not existing[index].get("id"):
                match = index
        if match is None:
            raise ValidationError({"complaint": "Complaint not found on this Job Card."})
        item = dict(existing[match])
        current = manual_complaint_status(item)
        if (current == desired and item.get("resolutionNotes", "") == notes
                and item.get("workReference", "") == work_reference
                and item.get("status") == desired):
            return response.Response({"complaint": item, "job": JobSerializer(job).data})
        timestamp = timezone.now().isoformat()
        item["id"] = item.get("id") or str(uuid4())
        history = list(item.get("statusHistory") or [])
        history.append({
            "from": current, "to": desired, "notes": notes,
            "workReference": work_reference, "by": str(request.user.pk),
            "at": timestamp,
        })
        item.update({
            "status": desired, "resolutionNotes": notes,
            "workReference": work_reference,
            "statusUpdatedAt": timestamp, "statusUpdatedBy": str(request.user.pk),
            "statusHistory": history,
        })
        existing[match] = item
        job.complaints = existing
        job.save(update_fields=["complaints", "updated_at"])
        record_job_event(
            job, request.user, "Complaint Status Updated",
            f"Complaint {item['id']}: {current} → {desired}",
            {"complaintId": str(item["id"]), "from": current, "to": desired,
             "notes": notes, "workReference": work_reference},
        )
        return response.Response({"complaint": item, "job": JobSerializer(job).data})

    @decorators.action(detail=True,methods=["get","post"],url_path="workflow")
    @transaction.atomic
    def workflow(self,request,pk=None):
        """Get durable current stage or explicitly complete one validated stage."""
        job = self.get_object()
        if request.method == "GET":
            return response.Response(workflow_state(job))

        job = Job.objects.select_for_update().get(pk=job.pk, company=job.company)
        state = workflow_state(job)
        stage = request.data.get("stage")
        if stage not in STAGES:
            raise ValidationError({"stage":"Unknown Job Card stage."})
        if stage in state["completed"]:
            raise ValidationError({"stage": "This Job Card stage is already completed."})
        if stage != state["current"]:
            raise ValidationError({"stage": f"Complete {state['current']} before {stage}."})

        if stage == "overview":
            if not job.customer_id or not job.vehicle_id:
                raise ValidationError({"stage":"Select a customer and vehicle first."})
        elif stage == "inspection":
            if (job.inspection or {}).get("status") != "Completed":
                raise ValidationError({"stage":"Finish the vehicle inspection checklist first."})
        elif stage == "estimate":
            # Approve one explicit, immutable version. A stale browser must
            # never accidentally authorize a newer quotation.
            estimate = job.estimates.select_for_update().order_by("-version", "-created_at").first()
            if not estimate:
                raise ValidationError({"stage":"Create an estimate before approval."})
            selected_id = request.data.get("estimateId")
            if selected_id is not None and str(selected_id) != str(estimate.pk):
                raise ValidationError({"estimateId": "The displayed estimate is no longer the latest version. Refresh before approving."})
            selected_version = request.data.get("estimateVersion")
            if selected_version is not None and str(selected_version) != str(estimate.version):
                raise ValidationError({"estimateVersion": "The displayed estimate version changed. Refresh before approving."})
            if estimate.status != "Draft":
                raise ValidationError({"stage":"Only the latest draft estimate can be approved. Save a revised estimate first."})
            if (job.inspection or {}).get("revisedAt"):
                revised = parse_datetime(job.inspection["revisedAt"])
                if revised and estimate.created_at < revised:
                    raise ValidationError({"stage": "Inspection changed. Save a revised estimate before approval."})
            if not request.data.get("approveEstimate"):
                raise ValidationError({"stage":"Confirm customer estimate approval to continue."})
            estimate.status = "Approved"
            estimate.approved_at = timezone.now()
            estimate.save(update_fields=["status","approved_at","updated_at"])
            # A staff attestation is not equivalent to an authenticated
            # customer e-signature. Keep the source of confirmation explicit.
            estimate_approval = {
                "status": "Approved", "estimateId": str(estimate.pk),
                "version": estimate.version, "total": str(estimate.total),
                "confirmedByStaffId": str(request.user.pk),
                "approvedAt": estimate.approved_at.isoformat(),
                "confirmationSource": "workshop_staff_attestation",
            }
        elif stage == "work":
            if not request.data.get("confirmWorkDone"):
                raise ValidationError({"stage":"Confirm that workshop work is complete."})
            if unresolved_complaints(job) and request.data.get("confirmUnresolvedComplaints") is not True:
                raise ValidationError({"complaints": "Review unresolved customer complaints and explicitly acknowledge them before QC."})
            self._guard_active_timers(job, "QC")
        elif stage == "qc":
            qc = job.qc or {}
            if qc.get("status") != "Pass":
                raise ValidationError({"stage":"Quality Check must pass before billing."})
            checklist = qc.get("checklist") or []
            if not checklist or any(item.get("status") != "Pass" for item in checklist):
                raise ValidationError({"stage":"Every Quality Check item must be marked Pass."})
            self._guard_active_timers(job, "Ready for Delivery")
        elif stage == "invoice":
            from apps.invoices.models import Invoice
            if not Invoice.objects.filter(
                company=job.company, job=job, kind="invoice",
                status__in=["Finalized","Paid"],
            ).exists():
                raise ValidationError({"stage":"Finalize a linked invoice before completing delivery."})
            # Save handover metadata with the stage transition in one transaction.
            handover = request.data.get("delivery")
            if not isinstance(handover, dict):
                raise ValidationError({"delivery": "Enter final kilometres and customer acknowledgement."})
            raw_km = handover.get("finalKm")
            if isinstance(raw_km, bool) or not str(raw_km).isdigit():
                raise ValidationError({"finalKm": "Enter a valid whole-number final odometer reading."})
            final_km = int(raw_km)
            if final_km > 2147483647 or final_km < job.odometer:
                raise ValidationError({"finalKm": "Final kilometres cannot be less than check-in kilometres."})
            acknowledgement = str(handover.get("acknowledgedBy") or "").strip()
            if not 1 <= len(acknowledgement) <= 160:
                raise ValidationError({"acknowledgedBy": "Enter customer's name (maximum 160 characters)."})
            warranty_notes = str(handover.get("warrantyNotes") or "").strip()
            if len(warranty_notes) > 1500:
                raise ValidationError({"warrantyNotes": "Warranty notes must be 1500 characters or fewer."})
            self._guard_active_timers(job, "Delivered")
            delivery_record = {
                "finalKm": final_km,
                "acknowledgedBy": acknowledgement,
                "warrantyNotes": warranty_notes,
                "deliveredAt": timezone.now().isoformat(),
                "completedById": str(request.user.pk),
            }

        progress = dict(job.workflow_progress or {})
        if stage == "estimate":
            progress["estimateDecision"] = estimate_approval
            job.estimate_total = estimate.total
        if stage == "invoice":
            progress["delivery"] = delivery_record
        completed = set(state["completed"])
        completed.add(stage)
        progress.update({
            "started": True,
            "completed": [name for name in STAGES if name in completed],
            "lastCompletedAt": timezone.now().isoformat(),
        })
        if stage == "work" and unresolved_complaints(job):
            progress["complaintReview"] = {
                "unresolvedCount": len(unresolved_complaints(job)),
                "acknowledgedBy": str(request.user.pk),
                "acknowledgedAt": timezone.now().isoformat(),
            }
        old_status = job.status
        job.workflow_progress = progress
        job.status = NEXT_STATUS[stage]
        if stage == "invoice":
            job.delivered_at = timezone.now()
        fields = ["workflow_progress", "status", "delivered_at", "updated_at"]
        if stage == "estimate":
            fields.append("estimate_total")
        if sync_complaint_status(job):
            fields.append("complaints")
        job.save(update_fields=fields)
        JobActivity.objects.create(
            company=job.company,branch=job.branch,job=job,actor=request.user,
            event="Workflow Stage Completed",description=f"{stage} completed",
            from_status=old_status,to_status=job.status,
            metadata={"stage": stage, **({"estimateApproval": estimate_approval} if stage == "estimate" else {})},
        )
        return response.Response({
            **workflow_state(job),
            "job": JobSerializer(job).data,
        })

    @decorators.action(detail=True, methods=["post"], url_path="work/services")
    @transaction.atomic
    def work_service_add(self, request, pk=None):
        """Add an actual customer-billable catalogue service to the active Work stage.

        Customer revenue is stored in Job.work. The agreed mechanic charge is
        *informational* until posted by the existing approved payroll/outside
        labour workflow. This action never issues stock, pays staff, or invoices.
        A stable client requestId makes network retries idempotent.
        """
        from apps.services.models import Service
        from apps.accounts.models import User
        from django.core.exceptions import ValidationError as DjangoValidationError

        scoped = self.get_object()
        job = Job.objects.select_for_update().get(
            pk=scoped.pk, company_id=request.user.company_id,
        )
        if workflow_state(job)["current"] != "work":
            raise ValidationError({"stage": "Add actual services only during the Work stage."})

        request_id = request.data.get("requestId")
        try:
            from uuid import UUID
            normalized_request = str(UUID(str(request_id)))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({"requestId": "A stable UUID requestId is required for safe retries."})
        existing = next(
            (item for item in (job.work or []) if isinstance(item, dict)
             and item.get("requestId") == normalized_request), None,
        )
        if existing:
            return response.Response({"item": existing, "job": JobSerializer(job).data})

        service_id = request.data.get("serviceId")
        try:
            service = Service.objects.get(
                pk=service_id, company=job.company, is_active=True,
            )
        except (Service.DoesNotExist, ValueError, TypeError, DjangoValidationError):
            raise ValidationError({"serviceId": "Choose an active service in this workshop."})

        def decimal_field(name, default, low, high, *, positive=False):
            raw = request.data.get(name, default)
            try:
                value = Decimal(str(raw))
            except (ArithmeticError, ValueError, TypeError):
                raise ValidationError({name: "Enter a valid decimal number."})
            if (not value.is_finite() or value.as_tuple().exponent < -2 or
                    value < low or value > high or (positive and value == 0)):
                raise ValidationError({name: "Use a valid amount with at most two decimals."})
            return value.quantize(Decimal("0.01"))

        quantity = decimal_field("quantity", 1, Decimal("0.01"), Decimal("10000"), positive=True)
        customer_rate = decimal_field(
            "customerRate", service.price, Decimal("0"), Decimal("10000000"),
        )
        worker_charge = decimal_field(
            "mechanicCost", 0, Decimal("0"), Decimal("10000000"),
        )
        mechanic = None
        mechanic_id = request.data.get("mechanicId")
        if mechanic_id:
            try:
                mechanic = User.objects.get(
                    pk=mechanic_id, company=job.company, is_active=True,
                )
            except (User.DoesNotExist, ValueError, TypeError, DjangoValidationError):
                raise ValidationError({"mechanicId": "Mechanic must be an active user in this workshop."})

        line = {
            "id": str(uuid4()), "requestId": normalized_request,
            "serviceId": str(service.pk), "service": service.name,
            "serviceCode": service.code, "quantity": str(quantity),
            "customerRate": str(customer_rate),
            "customerCharge": str((quantity * customer_rate).quantize(Decimal("0.01"))),
            "mechanicId": str(mechanic.pk) if mechanic else None,
            "mechanicName": mechanic.name if mechanic else "",
            "agreedWorkerCharge": str(worker_charge),
            "workerChargePosted": False,
            "billable": True,
            "hsnSac": service.hsn_sac, "taxRate": str(service.tax_rate),
            "taxTreatment": service.tax_treatment,
            "unit": service.unit,
            "addedAt": timezone.now().isoformat(),
            "addedBy": str(request.user.pk),
        }
        job.work = [line, *(job.work or [])]
        job.save(update_fields=["work", "updated_at"])
        record_job_event(
            job, request.user, "Job Service Added",
            f"Added {service.name} x {quantity}",
            {"serviceId": str(service.pk), "workId": line["id"], "requestId": normalized_request},
        )
        return response.Response(
            {"item": line, "job": JobSerializer(job).data},
            status=status.HTTP_201_CREATED,
        )

    @decorators.action(detail=True, methods=["post"], url_path="work/skip")
    @transaction.atomic
    def work_skip(self, request, pk=None):
        """Skip an unused Work stage, keeping QC and billing mandatory.

        Do not permit this after labour, contractor charges, or parts have
        been recorded. Use Finish Work for jobs where work actually occurred.
        """
        from apps.payroll.models import JobWorkSession
        from apps.inventory.models import StockMovement
        from .models import OutsideLabourCharge

        scoped = self.get_object()
        job = Job.objects.select_for_update().get(pk=scoped.pk, company=scoped.company)
        if workflow_state(job)["current"] != "work":
            raise ValidationError({"stage": "Only the active Work stage can be skipped."})
        reason = request.data.get("reason")
        if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 160:
            raise ValidationError({"reason": "Enter a skip reason between 3 and 160 characters."})
        reason = reason.strip()
        if (
            job.work or job.work_updates
            or JobWorkSession.objects.filter(company=job.company, job=job).exists()
            or OutsideLabourCharge.objects.filter(company=job.company, job=job).exists()
            or job.parts.filter(issued=True).exists()
            or job.photos.filter(photo_type="work_update").exists()
            or StockMovement.objects.filter(
                company=job.company, job=job,
                movement_type__in=("job_issue", "return"),
            ).exists()
            or any(
                item.get("status", "Purchased") == "Purchased"
                for item in (job.parts_workflow or {}).get("outsideParts", [])
                if isinstance(item, dict)
            )
        ):
            raise ValidationError({
                "work": "Work, labour, or parts have already been recorded. Finish Work normally instead of skipping."
            })
        self._guard_active_timers(job, "QC")
        if unresolved_complaints(job) and request.data.get("confirmUnresolvedComplaints") is not True:
            raise ValidationError({"complaints": "Review unresolved customer complaints before skipping work and advancing to QC."})

        now = timezone.now().isoformat()
        progress = dict(job.workflow_progress or {})
        if unresolved_complaints(job):
            progress["complaintReview"] = {
                "unresolvedCount": len(unresolved_complaints(job)),
                "acknowledgedBy": str(request.user.pk),
                "acknowledgedAt": now,
            }
        completed = set(workflow_state(job)["completed"])
        completed.add("work")
        skipped = set(progress.get("skipped") or [])
        skipped.add("work")
        skip_details = dict(progress.get("skipDetails") or {})
        skip_details["work"] = {
            "reason": reason, "skippedAt": now,
            "skippedById": str(request.user.pk),
        }
        progress.update({
            "started": True,
            "completed": [stage for stage in STAGES if stage in completed],
            "skipped": [stage for stage in STAGES if stage in skipped],
            "skipDetails": skip_details,
            "lastSkippedAt": now,
        })
        previous = job.status
        job.workflow_progress = progress
        job.status = NEXT_STATUS["work"]
        fields = ["workflow_progress", "status", "updated_at"]
        if sync_complaint_status(job):
            fields.append("complaints")
        job.save(update_fields=fields)
        JobActivity.objects.create(
            company=job.company, branch=job.branch, job=job,
            actor=request.user, event="Work Skipped",
            description=reason, from_status=previous, to_status=job.status,
            metadata={"stage": "work", "reason": reason, "skippedAt": now},
        )
        return response.Response({
            **workflow_state(job),
            "job": JobSerializer(job).data,
        })

    @decorators.action(detail=True,methods=["get"],url_path="inspection")
    def inspection_detail(self,request,pk=None):
        from apps.accounts.permissions import user_has_permission
        job=self.get_object()
        data = dict(job.inspection or {
            "status":"Not Started","checklist":{},"findings":[],
            "diagnosticScan":{"performed":False,"codes":[]},"photos":[],
            "summary":{"totalChecks":0,"good":0,"needsAttention":0,"issuesFound":0,"criticalIssues":0,"recommendedRepairs":0}
        })
        reason = self._inspection_skip_block_reason(job)
        if not user_has_permission(request.user, "jobs.edit"):
            reason = "You do not have permission to skip an inspection."
        data["skipAllowed"] = not bool(reason)
        data["skipBlockedReason"] = reason or ""
        return response.Response(data)

    @decorators.action(detail=True,methods=["post"],url_path="inspection/start")
    @transaction.atomic
    def inspection_start(self,request,pk=None):
        scoped=self.get_object()
        job=Job.objects.select_for_update().get(pk=scoped.pk,company_id=request.user.company_id)
        if (job.inspection or {}).get("status") in {"Skipped", "Completed"}:
            raise ValidationError({"inspection": "Resolved inspections cannot be restarted."})
        if workflow_state(job)["current"] != "inspection":
            raise ValidationError({"stage":"Complete Overview before starting Inspection, or return to the current stage."})
        data=dict(job.inspection or {})
        data.setdefault("checklist",{}); data.setdefault("findings",[]); data.setdefault("diagnosticScan",{"performed":False,"codes":[]}); data.setdefault("photos",[])
        data["status"]="In Progress"; data["startedAt"]=timezone.now().isoformat()
        job.inspection=data; job.status=Job.STATUS_INSPECTION; job.save(update_fields=["inspection","status","updated_at"])
        return response.Response(data)

    @decorators.action(detail=True,methods=["patch"],url_path="inspection/checklist")
    @transaction.atomic
    def inspection_checklist(self,request,pk=None):
        scoped=self.get_object()
        job=Job.objects.select_for_update().get(pk=scoped.pk,company_id=request.user.company_id)
        self._assert_inspection_editable(job)
        item = str(request.data.get("itemName") or "").strip()
        check_status = request.data.get("status", "Not Checked")
        if not item or len(item) > 120 or check_status not in {"Good", "Needs Attention", "Critical", "Not Checked", "Not Applicable"}:
            raise ValidationError({"checklist": "Select a valid inspection item and status."})
        data=dict(job.inspection or {}); checklist=dict(data.get("checklist") or {})
        previous = checklist.get(item,"Not Checked")
        checklist[item] = check_status
        data["checklist"]=checklist; job.inspection=data; job.save(update_fields=["inspection","updated_at"])
        if previous != check_status: self._audit_inspection_change(job, f"{item} updated", previous, check_status)
        return response.Response(job.inspection)

    @decorators.action(detail=True, methods=["post"], url_path="inspection/findings")
    @transaction.atomic
    def inspection_findings(self, request, pk=None):
        scoped = self.get_object()
        job = Job.objects.select_for_update().get(pk=scoped.pk, company=scoped.company)
        self._assert_inspection_editable(job)
        payload = validate_finding(request.data)
        data = dict(job.inspection or {})
        findings = list(data.get("findings") or [])
        if len(findings) >= 200:
            raise ValidationError({"findings": "Maximum 200 findings per Job Card."})
        finding = {
            **payload,
            "id": f"FND-{uuid4().hex[:10].upper()}",
            "dateTime": timezone.now().isoformat(),
            "addedToEstimate": False,
            "estimateStatus": "Not Added",
        }
        findings.insert(0, finding)
        data["findings"] = findings
        job.inspection = data
        job.save(update_fields=["inspection", "updated_at"])
        self._audit_inspection_change(job, "Finding added", None, finding)
        record_job_event(job, request.user, "Inspection Finding Added",
                         finding["description"], {"findingId": finding["id"],
                         "severity": finding["severity"]})
        return response.Response(finding, status=status.HTTP_201_CREATED)

    @decorators.action(detail=True, methods=["put", "delete"],
                       url_path=r"inspection/findings/(?P<finding_id>[^/.]+)")
    @transaction.atomic
    def inspection_finding_detail(self, request, pk=None, finding_id=None):
        scoped = self.get_object()
        job = Job.objects.select_for_update().get(pk=scoped.pk, company=scoped.company)
        self._assert_inspection_editable(job)
        data = dict(job.inspection or {})
        findings = list(data.get("findings") or [])
        index = next((i for i, entry in enumerate(findings)
                      if str(entry.get("id")) == str(finding_id)), None)
        if index is None:
            return response.Response({"message": "Finding not found."}, status=404)
        original = dict(findings[index])
        if request.method == "DELETE":
            if original.get("addedToEstimate"):
                raise ValidationError({
                    "finding": "Remove this finding from the draft estimate before deleting it.",
                })
            removed = findings.pop(index)
            data["findings"] = findings
            job.inspection = data
            job.save(update_fields=["inspection", "updated_at"])
            self._audit_inspection_change(job, "Finding removed", removed, None)
            record_job_event(job, request.user, "Inspection Finding Removed",
                             str(removed.get("description") or ""),
                             {"findingId": str(finding_id)})
            return response.Response(removed)

        editable_keys = (
            "description", "title", "category", "severity", "recommendedAction",
            "technicianFinding", "requiredParts", "estimatedLabourTime",
            "estimatedLabourCharge", "estimatedPartCharge", "estimatedCost",
            "complaintId", "notes",
        )
        combined = {key: original[key] for key in editable_keys if key in original}
        combined.update({
            key: value for key, value in request.data.items() if key in editable_keys
        })
        validated = validate_finding(combined)
        finding = {**original, **validated}
        # Never accept client-supplied IDs, server timestamps, approval flags,
        # estimate status, or raw image contents.
        findings[index] = finding
        data["findings"] = findings
        job.inspection = data
        job.save(update_fields=["inspection", "updated_at"])
        self._audit_inspection_change(job, "Finding edited", original, finding)
        record_job_event(job, request.user, "Inspection Finding Updated",
                         str(finding.get("description") or ""),
                         {"findingId": str(finding_id)})
        return response.Response(finding)

    @decorators.action(detail=True, methods=["post"], url_path="inspection/diagnostic")
    @transaction.atomic
    def inspection_diagnostic(self, request, pk=None):
        scoped = self.get_object()
        job = Job.objects.select_for_update().get(pk=scoped.pk, company=scoped.company)
        self._assert_inspection_editable(job)
        payload = validate_diagnostic(request.data)
        data = dict(job.inspection or {})
        scan = dict(data.get("diagnosticScan") or {})
        codes = list(scan.get("codes") or [])
        if len(codes) >= 200:
            raise ValidationError({"diagnostic": "Maximum 200 diagnostic codes."})
        code = {
            **payload, "id": f"OBD-{uuid4().hex[:8].upper()}",
            "dateTime": timezone.now().isoformat(),
            "recordedById": str(request.user.pk),
        }
        codes.append(code)
        scan.update({
            "performed": True, "scanTime": timezone.now().isoformat(),
            "codes": codes,
        })
        data["diagnosticScan"] = scan
        job.inspection = data
        job.save(update_fields=["inspection", "updated_at"])
        self._audit_inspection_change(job, "Diagnostic code added", None, code)
        record_job_event(job, request.user, "Diagnostic Code Recorded",
                         code["code"], {"diagnosticId": code["id"]})
        return response.Response(code, status=status.HTTP_201_CREATED)

    @decorators.action(detail=True, methods=["post"], url_path="inspection/photos")
    @transaction.atomic
    def inspection_photo(self, request, pk=None):
        """Reference a real private image; never persist Base64 or an arbitrary URL."""
        from apps.vehicles.models import VehicleInspectionPhoto
        from uuid import UUID

        scoped = self.get_object()
        job = Job.objects.select_for_update().get(pk=scoped.pk, company=scoped.company)
        self._assert_inspection_editable(job)
        raw_photo_id = request.data.get("photoId")
        try:
            photo_id = UUID(str(raw_photo_id))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({
                "photoId": "Upload the photo through the private inspection photo API first.",
            })
        saved = VehicleInspectionPhoto.objects.filter(
            pk=photo_id, company=job.company, vehicle=job.vehicle, job=job,
        ).first()
        if saved is None or not saved.file:
            raise ValidationError({"photoId": "Photo does not belong to this Job Card."})
        data = dict(job.inspection or {})
        photos = list(data.get("photos") or [])
        existing = next((p for p in photos
                         if str(p.get("privatePhotoId")) == str(saved.pk)), None)
        if existing:
            return response.Response(existing)
        if len(photos) >= 250:
            raise ValidationError({"photos": "Maximum 250 photos per inspection."})
        record = {
            "id": f"PH-{saved.pk.hex[:12].upper()}",
            "privatePhotoId": str(saved.pk),
            "category": saved.category,
            "caption": saved.caption,
            "dateTime": saved.captured_at.isoformat(),
        }
        photos.append(record)
        data["photos"] = photos
        job.inspection = data
        job.save(update_fields=["inspection", "updated_at"])
        self._audit_inspection_change(job, "Inspection photo linked", None, record)
        record_job_event(job, request.user, "Inspection Photo Linked",
                         saved.caption or "Private inspection photo",
                         {"photoId": str(saved.pk)})
        return response.Response(record, status=status.HTTP_201_CREATED)

    @decorators.action(detail=True,methods=["post"],url_path="inspection/complete")
    @transaction.atomic
    def inspection_complete(self,request,pk=None):
        scoped=self.get_object()
        job=Job.objects.select_for_update().get(pk=scoped.pk,company_id=request.user.company_id)
        data=dict(job.inspection or {})
        if data.get("status") == "Completed":
            raise ValidationError({"inspection": "Inspection is already completed. Use Edit Inspection before estimate approval."})
        if data.get("status") == "Skipped":
            raise ValidationError({"inspection": "Skipped inspections cannot be completed retroactively."})
        self._assert_inspection_editable(job)
        checked = data.get("checklist") or {}
        if not any(value in {"Good","Needs Attention","Critical"} for value in checked.values()):
            raise ValidationError({"inspection":"Record inspection checks before completing."})
        data["status"]="Completed"; data["completedAt"]=timezone.now().isoformat()
        job.inspection=data; job.status=Job.STATUS_ESTIMATE_PENDING if request.data.get("needsApproval",True) else job.status
        job.save(update_fields=["inspection","status","updated_at"])
        return response.Response({"inspection":data,"newJobStatus":job.status})


    @decorators.action(detail=True, methods=["post"], url_path="inspection/skip")
    @transaction.atomic
    def inspection_skip(self, request, pk=None):
        """Distinct, auditable inspection exit; never synthesize checklist results.

        Lock the tenant-scoped Job row to prevent a simultaneous skip, completion,
        or second skip request from recording a duplicate audit event.
        """
        scoped = self.get_object()  # RolePermission(jobs.edit) + tenant scope
        job = Job.objects.select_for_update().select_related("company").get(
            pk=scoped.pk, company_id=request.user.company_id,
        )
        reason = request.data.get("reason")
        if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 160:
            raise ValidationError({"reason": "Enter a skip reason (3–160 characters)."})
        reason = reason.strip()
        blocked = self._inspection_skip_block_reason(job)
        if blocked:
            raise ValidationError({"inspection": blocked})

        now = timezone.now()
        inspection = dict(job.inspection or {})
        inspection.update({
            "status": "Skipped",
            "skipReason": reason,
            "skippedAt": now.isoformat(),
            "skippedById": str(request.user.pk),
            "skippedBy": str(getattr(request.user, "name", "") or "").strip(),
        })
        progress = dict(job.workflow_progress or {})
        completed = set(workflow_state(job)["completed"])
        completed.add("inspection")
        progress["started"] = True
        progress["completed"] = [stage for stage in STAGES if stage in completed]
        progress["skipped"] = sorted(set(progress.get("skipped") or []) | {"inspection"})
        progress["lastSkippedAt"] = now.isoformat()

        old_status = job.status
        job.inspection = inspection
        job.workflow_progress = progress
        job.status = Job.STATUS_ESTIMATE_PENDING
        job.save(update_fields=["inspection", "workflow_progress", "status", "updated_at"])
        record_job_event(
            job, request.user, "Inspection Skipped",
            f"Vehicle inspection skipped: {reason}",
            {
                "stage": "inspection", "reason": reason,
                "skippedAt": now.isoformat(), "staffId": str(request.user.pk),
                "fromStatus": old_status, "toStatus": job.status,
            },
        )
        return response.Response({
            "inspection": inspection,
            "newJobStatus": job.status,
            "workflow": workflow_state(job),
            "job": JobSerializer(job).data,
        })

    @decorators.action(detail=True,methods=["post"],url_path=r"inspection/findings/(?P<finding_id>[^/.]+)/add-to-estimate")
    def inspection_add_to_estimate(self,request,pk=None,finding_id=None):
        job=self.get_object()
        self._assert_inspection_editable(job)
        data=dict(job.inspection or {}); findings=list(data.get("findings") or [])
        finding=next((x for x in findings if str(x.get("id"))==str(finding_id)),None)
        if not finding: return response.Response({"message":"Finding not found."},status=404)
        finding["addedToEstimate"]=True; finding["estimateStatus"]="Added to Estimate"; job.inspection={**data,"findings":findings}
        job.save(update_fields=["inspection","updated_at"])
        self._audit_inspection_change(job, "Finding marked for estimate", None, finding)
        return response.Response(finding)

    @decorators.action(
        detail=True, methods=["post"],
        url_path=r"estimates/(?P<estimate_id>[^/.]+)/decision",
    )
    @transaction.atomic
    def estimate_decision(self,request,pk=None,estimate_id=None):
        """Reject an estimate; approval is an explicit workflow completion."""
        job = Job.objects.select_for_update().get(pk=self.get_object().pk, company=request.user.company)
        if request.data.get("decision") != "Rejected":
            raise ValidationError({"decision":"Use Complete & Continue to approve an estimate."})
        if workflow_state(job)["current"] != "estimate":
            raise ValidationError({"estimate":"Estimate decisions are locked after customer approval."})
        estimate = job.estimates.select_for_update().filter(pk=estimate_id).first()
        if not estimate:
            raise ValidationError({"estimate":"Estimate not found for this Job Card."})
        latest = job.estimates.order_by("-version", "-created_at").first()
        if not latest or latest.pk != estimate.pk:
            raise ValidationError({"estimate":"Only the latest estimate can be rejected."})
        if estimate.status != "Draft":
            raise ValidationError({"estimate":"Only a draft estimate can be rejected."})
        estimate.status = "Rejected"
        estimate.save(update_fields=["status","updated_at"])
        record_job_event(job, request.user, "Estimate Rejected",
                         f"Estimate V{estimate.version} rejected",
                         {"estimateId": str(estimate.pk), "version": estimate.version})
        return response.Response(JobEstimateSerializer(estimate).data)

    @decorators.action(detail=True, methods=["post"], url_path="estimate/skip")
    @transaction.atomic
    def estimate_skip(self, request, pk=None):
        """Explicitly skip only the current Estimate stage; never approve a quote."""
        job = Job.objects.select_for_update().get(
            pk=self.get_object().pk, company=request.user.company,
        )
        state = workflow_state(job)
        if state["current"] != "estimate":
            raise ValidationError({"estimate": "Only the active Estimate stage can be skipped."})
        if (job.inspection or {}).get("status") not in {"Completed", "Skipped"}:
            raise ValidationError({"inspection": "Complete or explicitly skip inspection before skipping an estimate."})
        if job.estimates.filter(status="Approved").exists():
            raise ValidationError({"estimate": "An approved estimate cannot be skipped."})
        if job.company.require_customer_estimate_approval:
            raise ValidationError({"estimate": "Workshop policy requires customer estimate approval."})
        inspection = job.inspection or {}
        if inspection.get("requiresEstimateApproval") or inspection.get("mandatoryEstimateApproval"):
            raise ValidationError({"estimate": "This job requires an approved customer estimate."})
        if ("Critical" in (inspection.get("checklist") or {}).values() or any(
            isinstance(item, dict) and str(item.get("severity") or "").lower() == "critical"
            for item in (inspection.get("findings") or [])
        )):
            raise ValidationError({"estimate": "Critical safety findings require estimate review and approval."})
        reasons = {
            "Estimate Not Required", "Quick Service",
            "Customer Declined Estimate", "Other",
        }
        reason = request.data.get("reason")
        details = str(request.data.get("details") or "").strip()
        if reason not in reasons:
            raise ValidationError({"reason": "Choose a supported skip reason."})
        if len(details) > 500 or (reason == "Other" and len(details) < 5):
            raise ValidationError({"details": "For Other, enter a reason (5–500 characters)."})
        timestamp = timezone.now()
        decision = {
            "status": "Skipped", "reason": reason, "details": details,
            "skippedBy": str(request.user.pk), "skippedAt": timestamp.isoformat(),
        }
        progress = dict(job.workflow_progress or {})
        completed = set(state["completed"])
        completed.add("estimate")
        skipped = set(progress.get("skipped") or [])
        skipped.add("estimate")
        skip_details = dict(progress.get("skipDetails") or {})
        skip_details["estimate"] = decision
        progress.update({
            "started": True,
            "completed": [name for name in STAGES if name in completed],
            "skipped": [name for name in STAGES if name in skipped],
            "skipDetails": skip_details,
            "lastCompletedAt": timestamp.isoformat(),
            "lastSkippedAt": timestamp.isoformat(),
            "estimateDecision": decision,
        })
        job.workflow_progress = progress
        job.status = NEXT_STATUS["estimate"]
        job.save(update_fields=["workflow_progress", "status", "updated_at"])
        record_job_event(
            job, request.user, "Estimate Skipped",
            f"Estimate skipped: {reason}",
            {**decision, "stage": "estimate"},
        )
        return response.Response({**workflow_state(job), "job": JobSerializer(job).data})

    def _quick_estimate_values(self, data):
        lines = data.get("lines")
        if not isinstance(lines, list) or not 1 <= len(lines) <= 75:
            raise ValidationError({"lines": "Add 1 to 75 work or part items."})
        try:
            tax_rate = Decimal(str(data.get("taxPercent", 0)))
            discount = Decimal(str(data.get("discount", 0)))
        except (TypeError, ValueError, ArithmeticError):
            raise ValidationError({"total": "Tax and discount must be valid numbers."})
        if not tax_rate.is_finite() or not discount.is_finite():
            raise ValidationError({"total": "Tax and discount must be finite numbers."})
        if tax_rate.as_tuple().exponent < -2 or discount.as_tuple().exponent < -2:
            raise ValidationError({"total": "Tax and discount may have at most two decimal places."})
        if not (Decimal("0") <= tax_rate <= Decimal("100")) or discount < 0:
            raise ValidationError({"total": "Tax must be 0-100%, and discount cannot be negative."})
        items = []
        subtotal = Decimal("0")
        for row in lines:
            if not isinstance(row, dict):
                raise ValidationError({"lines": "Invalid estimate item."})
            label = str(row.get("description") or "").strip()
            if not label or len(label) > 250:
                raise ValidationError({"lines": "Every item needs a description (max 250 characters)."})
            try:
                quantity = Decimal(str(row.get("quantity", 1)))
                price = Decimal(str(row.get("unitPrice", 0)))
            except (TypeError, ValueError, ArithmeticError):
                raise ValidationError({"lines": "Quantity and price must be numbers."})
            if not quantity.is_finite() or not price.is_finite():
                raise ValidationError({"lines": "Quantity and price must be finite numbers."})
            if quantity.as_tuple().exponent < -2 or price.as_tuple().exponent < -2:
                raise ValidationError({"lines": "Quantity and price may have at most two decimal places."})
            if not (Decimal("0") < quantity <= Decimal("10000")) or not (Decimal("0") <= price <= Decimal("10000000")):
                raise ValidationError({"lines": "Enter a positive quantity and non-negative price."})
            quantity = quantity.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            price = price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total = (quantity * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            subtotal += total
            item_type = str(row.get("type") or "Service")
            if item_type not in {"Part", "Labour", "Service", "Other"}:
                raise ValidationError({"lines": "Choose Part, Labour, Service or Other for the item type."})
            catalog_id = str(row.get("catalogId") or "").strip()
            line = {
                "description": label,
                "type": item_type,
                "quantity": str(quantity), "unitPrice": str(price), "total": str(total),
            }
            if "taxRate" in row and row["taxRate"] not in (None, ""):
                line["taxRate"] = str(gst_rate(row["taxRate"]))
            selected_label = str(row.get("taxChoice") or "").strip()
            treatment = str(row.get("taxTreatment") or "").strip()
            confirmed = row.get("taxTreatmentConfirmed") is True
            if selected_label == "none":
                if line.get("taxRate") != "0":
                    raise ValidationError({"taxChoice": "None / No GST requires a 0% line rate."})
                if treatment not in {"exempt", "nil_rated", "non_gst", "out_of_scope"} or not confirmed:
                    raise ValidationError({"taxTreatment": "Explicitly classify and confirm this zero-tax supply."})
                line["taxChoice"] = "none"
                line["taxTreatment"] = treatment
                line["taxTreatmentConfirmed"] = True
            elif selected_label:
                selection = find_gst_choice(selected_label)
                if not selection or "taxRate" not in line or (
                    Decimal(selection["totalRate"]) != Decimal(line["taxRate"])
                ):
                    raise ValidationError({
                        "taxChoice": "Selected GST label does not match the line's total rate."
                    })
                if treatment and treatment != "taxable":
                    raise ValidationError({"taxTreatment": "Remove zero-tax classification from taxed supplies."})
                line["taxChoice"] = selected_label
            elif treatment and treatment != "taxable":
                raise ValidationError({"taxTreatment": "Select None / No GST to classify a zero-tax supply."})
            hsn = str(row.get("hsnSac") or "").strip()
            if len(hsn) > 20 or (hsn and not hsn.isdigit()):
                raise ValidationError({"hsnSac": "HSN/SAC must contain up to 20 digits."})
            if line.get("taxChoice") == "none" and not hsn:
                raise ValidationError({"hsnSac": "HSN / SAC is required to justify a non-taxed supply."})
            if hsn:
                line["hsnSac"] = hsn
            if catalog_id:
                # Quote lines keep their linked catalog identity without moving stock.
                # Reject unrelated and cross-company catalog references.
                from apps.services.models import Service
                from apps.inventory.models import StockItem
                from django.core.exceptions import ValidationError as DjangoValidationError
                catalog_model = StockItem if item_type == "Part" else Service
                try:
                    linked = catalog_model.objects.filter(
                        pk=catalog_id, company=self.request.user.company
                    ).exists()
                except (ValueError, DjangoValidationError):
                    linked = False
                if not linked:
                    raise ValidationError({"catalogId": "Selected catalog item was not found in this workshop."})
                line["catalogId"] = catalog_id
            items.append(line)
        subtotal = subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if discount > subtotal:
            raise ValidationError({"discount": "Discount cannot exceed subtotal."})
        discount = discount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        gst_input = data.get("gst")
        if gst_input is not None:
            job = self.get_object()
            context = gst_context(
                company=job.company, branch=job.branch, customer=job.customer,
                config=gst_input,
            )
            if context["mode"] == "gst":
                for line in items:
                    if "taxRate" not in line:
                        raise ValidationError({"taxRate": "Set a classified GST rate for every work and part item."})
                    if line["taxRate"] == "0":
                        if line.get("taxChoice") != "none" or not line.get("taxTreatmentConfirmed"):
                            raise ValidationError({"taxTreatment": "Zero GST requires an explicit confirmed tax classification."})
                        # No arbitrary 0% for registered workshops: tax specialists
                        # configure the eligible HSN/SAC + treatment pairs, rather
                        # than us guessing exemption rules in application code.
                        eligible = getattr(settings, "ESTIMATE_ZERO_GST_CLASSIFICATIONS", {})
                        permitted = eligible.get(line.get("hsnSac", ""), ()) if isinstance(eligible, dict) else ()
                        if line.get("taxTreatment") not in permitted:
                            raise ValidationError({"taxTreatment": "This HSN/SAC is not configured for the selected zero-GST treatment. Contact your tax administrator."})
                    selection = find_gst_choice(line.get("taxChoice", ""))
                    if selection and selection["taxMode"] != context["taxMode"]:
                        raise ValidationError({
                            "taxChoice": "Selected GST type does not match the legal Place of Supply."
                        })
            calculated = calculate_tax(items, discount, context["taxMode"])
            items = calculated["lines"]
            tax = calculated["tax"]
            grand = calculated["total"]
            context.update({
                "cgst": str(calculated["cgst"]),
                "sgst": str(calculated["sgst"]),
                "igst": str(calculated["igst"]),
                "taxable": str(calculated["taxable"]),
            })
            pricing = {"taxPercent": "0", "discount": str(discount), "gst": context}
        else:
            # Preserve existing callers and historical non-GST quote behavior.
            tax = ((subtotal - discount) * tax_rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            grand = subtotal - discount + tax
            pricing = {"taxPercent": str(tax_rate), "discount": str(discount)}
        if subtotal >= Decimal("10000000000") or grand >= Decimal("10000000000"):
            raise ValidationError({"total": "Estimate total is too large."})
        items.append({"_pricing": pricing})
        return {"items": items, "subtotal": subtotal, "tax": tax, "total": grand}

    @decorators.action(detail=True, methods=["post"], url_path="estimates/quick")
    @transaction.atomic
    def quick_estimate(self, request, pk=None):
        job = Job.objects.select_for_update().get(pk=self.get_object().pk, company=request.user.company)
        identity = estimate_identity(request.data)
        if identity:
            # The job row lock serializes concurrent requests for this quote.
            # A retry returns the original version without a second activity.
            for previous in job.estimates.order_by("-version", "-created_at"):
                saved = saved_estimate_identity(previous)
                if saved and saved[0] == identity[0]:
                    if saved[1] != identity[1]:
                        raise ValidationError({"requestId": "This request ID was already used with different estimate values."})
                    return response.Response(JobEstimateSerializer(previous).data)
        if workflow_state(job)["current"] != "estimate":
            raise ValidationError({"estimate": "Only the active Estimate stage can create a quotation."})
        values = self._quick_estimate_values(request.data)
        attach_identity(values["items"], identity)
        version = (job.estimates.order_by("-version").values_list("version", flat=True).first() or 0) + 1
        obj = JobEstimate.objects.create(
            company=job.company, branch=job.branch, job=job,
            version=version, status="Draft", **values,
        )
        JobActivity.objects.create(
            company=job.company, branch=job.branch, job=job,
            actor=request.user, event="Estimate Draft Saved",
            description=f"Estimate V{version} created",
            metadata={"estimateId": str(obj.pk), "total": str(obj.total)},
        )
        return response.Response(JobEstimateSerializer(obj).data, status=201)

    @decorators.action(detail=True, methods=["patch"],
                       url_path=r"estimates/quick/(?P<estimate_id>[^/.]+)")
    @transaction.atomic
    def quick_estimate_detail(self, request, pk=None, estimate_id=None):
        job = Job.objects.select_for_update().get(pk=self.get_object().pk, company=request.user.company)
        if workflow_state(job)["current"] != "estimate":
            raise ValidationError({"estimate": "Estimate editing is closed after approval."})
        draft = JobEstimate.objects.select_for_update().filter(
            pk=estimate_id, job=job, company=job.company,
        ).first()
        if not draft:
            raise ValidationError({"estimate": "Estimate not found for this Job Card."})
        if draft.status != "Draft":
            raise ValidationError({"estimate": "Only draft estimates can be edited."})
        latest = job.estimates.order_by("-version", "-created_at").first()
        if latest.pk != draft.pk:
            raise ValidationError({"estimate": "Only the latest draft estimate can be edited. Refresh the Job Card."})
        identity = estimate_identity(request.data)
        existing_identity = saved_estimate_identity(draft)
        if identity and existing_identity and identity[0] == existing_identity[0]:
            if identity[1] != existing_identity[1]:
                raise ValidationError({"requestId": "This request ID was already used with different estimate values."})
            return response.Response(JobEstimateSerializer(draft).data)
        values = self._quick_estimate_values(request.data)
        attach_identity(values["items"], identity)
        for field, value in values.items():
            setattr(draft, field, value)
        draft.save(update_fields=[*values.keys(), "updated_at"])
        JobActivity.objects.create(
            company=job.company, branch=job.branch, job=job,
            actor=request.user, event="Estimate Draft Updated",
            description=f"Estimate V{draft.version} updated",
            metadata={"estimateId": str(draft.pk), "total": str(draft.total)},
        )
        return response.Response(JobEstimateSerializer(draft).data)

    @decorators.action(detail=True,methods=["get","post"])
    def estimates(self,request,pk=None):
        job=self.get_object()
        if request.method=="POST":
            if workflow_state(job)["current"] != "estimate":
                raise ValidationError({"estimate": "Create estimates only during the Estimate stage."})
            if request.data.get("status", "Draft") != "Draft":
                raise ValidationError({"status": "Only draft estimates can be created."})
            s=JobEstimateSerializer(data=request.data); s.is_valid(raise_exception=True)
            version = (job.estimates.order_by("-version").values_list("version", flat=True).first() or 0) + 1
            obj=s.save(company=job.company,branch=job.branch,job=job,
                       version=version,status="Draft",approved_at=None)
            record_job_event(job, request.user, "Estimate Created",
                             f"Estimate V{obj.version} created", {"estimateId": str(obj.pk)})
            return response.Response(JobEstimateSerializer(obj).data,status=status.HTTP_201_CREATED)
        return response.Response(JobEstimateSerializer(job.estimates.all(),many=True).data)

    @decorators.action(
        detail=True, methods=["post"], url_path="photos/upload",
        parser_classes=[MultiPartParser, FormParser],
    )
    def upload_work_photo(self, request, pk=None):
        """Attach a private work photo with an editable caption to this Job Card."""
        job = self.get_object()
        image = request.FILES.get("image")
        if image is None:
            raise ValidationError({"image": "Select an image to upload."})
        if image.size <= 0 or image.size > 5 * 1024 * 1024:
            raise ValidationError({"image": "Choose an image under 5 MB."})

        allowed = {
            "image/jpeg": (b"\xff\xd8\xff", ".jpg"),
            "image/png": (b"\x89PNG\r\n\x1a\n", ".png"),
            "image/gif": (b"GIF8", ".gif"),
            "image/webp": (b"RIFF", ".webp"),
        }
        media_type = str(image.content_type or "").split(";")[0].lower().strip()
        if media_type not in allowed:
            raise ValidationError({"image": "Only JPEG, PNG, WebP and GIF photos are supported."})
        image.seek(0)
        header = image.read(16)
        image.seek(0)
        signature, extension = allowed[media_type]
        if not header.startswith(signature) or (media_type == "image/webp" and header[8:12] != b"WEBP"):
            raise ValidationError({"image": "Invalid image file contents."})

        caption = str(request.data.get("caption") or "").strip()
        if len(caption) > 255:
            raise ValidationError({"caption": "Caption must be 255 characters or fewer."})

        photo = JobPhoto(
            company=job.company, branch=job.branch, job=job,
            uploaded_by=request.user, photo_type="work_update",
            caption=caption,
        )
        photo.file.save(f"work-photo{extension}", image, save=False)
        try:
            photo.save()
        except Exception:
            photo.file.delete(save=False)
            raise
        JobActivity.objects.create(
            company=job.company, branch=job.branch, job=job,
            actor=request.user, event="Work Photo Added",
            description=caption or "Technician uploaded a work photo",
            metadata={"photoId": str(photo.pk)},
        )
        return response.Response(JobPhotoSerializer(photo).data, status=status.HTTP_201_CREATED)

    @decorators.action(
        detail=True, methods=["get"],
        url_path=r"photos/(?P<photo_id>[^/.]+)/file",
    )
    def work_photo_file(self, request, pk=None, photo_id=None):
        """Serve original bytes only after Job View permission and tenant scoping."""
        job = self.get_object()
        photo = get_object_or_404(JobPhoto, pk=photo_id, company=job.company, job=job)
        if not photo.file:
            raise NotFound("No uploaded file exists for this photo.")
        photo.file.open("rb")
        media_type = mimetypes.guess_type(photo.file.name)[0] or "application/octet-stream"
        if media_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            media_type = "application/octet-stream"
        result = FileResponse(photo.file, content_type=media_type)
        result["Cache-Control"] = "private, no-store"
        result["X-Content-Type-Options"] = "nosniff"
        return result

    @decorators.action(detail=True,methods=["get","post"])
    def photos(self,request,pk=None):
        job=self.get_object()
        if request.method=="POST":
            s=JobPhotoSerializer(data=request.data); s.is_valid(raise_exception=True)
            obj=s.save(company=job.company,branch=job.branch,job=job,uploaded_by=request.user)
            record_job_event(job, request.user, "Job Photo Added",
                             obj.caption or "Job Card photo added", {"photoId": str(obj.pk)})
            return response.Response(JobPhotoSerializer(obj).data,status=status.HTTP_201_CREATED)
        return response.Response(JobPhotoSerializer(job.photos.all(),many=True).data)

    @decorators.action(detail=True,methods=["get"])
    def activity(self,request,pk=None):
        job = self.get_object()  # Company-scoped; never expose another workshop's records.
        return response.Response(JobActivitySerializer(job.activities.select_related('actor'), many=True).data)

    @decorators.action(detail=True, methods=["post"], url_path="issue-part")
    @transaction.atomic
    def issue_part(self, request, pk=None):
        """Atomic Job Card issue: validate branch, quantity and available stock.

        Selecting stock in Work is read-only; only this endpoint deducts stock,
        creates the issued JobPart and its immutable stock ledger movement.
        """
        from decimal import InvalidOperation
        from django.core.exceptions import ValidationError as DjangoValidationError
        from apps.inventory.models import StockItem, StockMovement

        job = self.get_object()  # Company scoped; permissions also require jobs.edit + stock.edit.
        item_id = request.data.get("item")
        if not item_id:
            raise ValidationError({"item": "Select a valid stock item."})
        try:
            qty = Decimal(str(request.data.get("quantity", "")))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError({"quantity": "Enter a valid numeric quantity."})
        if (not qty.is_finite() or qty <= 0 or
                qty.as_tuple().exponent < -2 or qty > Decimal("99999999.99")):
            raise ValidationError({
                "quantity": "Enter a positive quantity with at most two decimal places."
            })
        try:
            item = StockItem.objects.select_for_update().get(
                pk=item_id, company=job.company
            )
        except (StockItem.DoesNotExist, ValueError, TypeError, DjangoValidationError):
            raise ValidationError({"item": "Stock item not found in this workshop."})

        expected_branch_id = job.branch_id or getattr(request.user, "branch_id", None)
        if item.branch_id and item.branch_id != expected_branch_id:
            raise ValidationError({"item": "This stock item belongs to another branch."})
        idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
        if idempotency_key and (len(idempotency_key) > 48 or not idempotency_key.isascii() or not all(ch.isalnum() or ch in "-_" for ch in idempotency_key)):
            raise ValidationError({"idempotencyKey": "Use an ASCII alphanumeric idempotency key (max 48 characters)."})
        movement_reference = f"job-issue:{job.pk}:{idempotency_key}" if idempotency_key else job.job_number
        if idempotency_key:
            existing = StockMovement.objects.filter(company=job.company, item=item, job=job, movement_type="job_issue", reference=movement_reference).first()
            if existing:
                if existing.quantity != -qty:
                    raise ValidationError({"idempotencyKey": "This key was already used for a different quantity."})
                part = job.parts.filter(item=item, issued=True, pk=existing.note.removeprefix("jobPart:")).first() if existing.note.startswith("jobPart:") else None
                if part:
                    return response.Response(JobPartSerializer(part).data, status=status.HTTP_200_OK)
                raise ValidationError({"idempotencyKey": "Existing stock issue requires reconciliation; no additional stock was deducted."})
        if item.available_quantity < qty:
            raise ValidationError({"quantity": "Insufficient available stock."})

        item.on_hand -= qty
        item.save(update_fields=["on_hand", "updated_at"])
        part = JobPart.objects.create(
            company=job.company, branch=job.branch or request.user.branch,
            job=job, item=item, quantity=qty, unit_price=item.selling_price, issued=True,
        )
        StockMovement.objects.create(
            company=job.company, branch=item.branch or job.branch or request.user.branch,
            item=item, movement_type="job_issue", quantity=-qty,
            unit_cost=item.cost_price, reference=movement_reference,
            note=f"jobPart:{part.pk}", job=job, created_by=request.user,
        )
        record_job_event(job, request.user, "Part Issued",
                         f"{item.name} × {qty}", {"partId": str(part.pk)})
        return response.Response(JobPartSerializer(part).data,
                                 status=status.HTTP_201_CREATED)
