from decimal import Decimal, ROUND_HALF_UP
from django.utils.dateparse import parse_datetime
from django.db import transaction
from django.utils import timezone
from rest_framework import decorators,response,status
from rest_framework.exceptions import ValidationError
from common.viewsets import CompanyScopedModelViewSet
from .models import Job,JobActivity,JobEstimate,JobPart,JobPhoto
from .serializers import JobSerializer,JobEstimateSerializer,JobPartSerializer,JobPhotoSerializer,JobActivitySerializer
from .workflow import STAGES, NEXT_STATUS, workflow_state, stage_index_from_status

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
        "inspection_add_to_estimate": "jobs.edit",
        "estimates": {"GET": "jobs.view", "POST": "jobs.edit"},
        "quick_estimate": "jobs.edit",
        "quick_estimate_detail": "jobs.edit",
        "estimate_decision": "jobs.edit",
        "photos": {"GET": "jobs.view", "POST": "jobs.edit"},
        "activity": "jobs.view",
        "issue_part": ["jobs.edit", "stock.edit"],
    }
    def get_queryset(self):
        qs=super().get_queryset(); st=self.request.query_params.get("status")
        return qs.filter(status=st) if st else qs
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

    def _assert_inspection_editable(self, job):
        """Completed checks may be corrected before customer approves an estimate."""
        current = workflow_state(job)["current"]
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

    def perform_update(self,serializer):
        job = self.get_object()
        next_status = serializer.validated_data.get("status")
        if "inspection" in serializer.validated_data:
            self._assert_inspection_editable(job)
            if (job.inspection or {}).get("status") == "Completed":
                raise ValidationError({
                    "inspection": "Use inspection edit actions to preserve revision history."
                })
        if next_status:
            unlocked = workflow_state(job)["current"]
            if stage_index_from_status(next_status) > STAGES.index(unlocked):
                raise ValidationError({"status": "Complete the current Job Card stage first."})
        self._guard_active_timers(job,next_status)
        serializer.save()


    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Accept the quick job form while preserving the UUID-based canonical API."""
        from apps.customers.models import Customer
        from apps.vehicles.models import Vehicle
        from django.db.models import Value
        from django.db.models.functions import Replace, Upper

        data = request.data.copy()
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
            chosen_customer = None
            chosen_id = data.get("customer")
            if chosen_id:
                chosen_customer = Customer.objects.filter(
                    company=company, pk=chosen_id, status="active"
                ).first()
                if chosen_customer is None:
                    raise ValidationError({"customer": "Select a valid active customer."})

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
                if not chosen_customer and "".join(filter(str.isdigit, customer.phone)) != "".join(filter(str.isdigit, phone)):
                    raise ValidationError({
                        "vehicleReg": "This registration belongs to another customer. Open the vehicle record to verify ownership."
                    })
                if isinstance(vehicle_value, str) and vehicle_value and str(vehicle.id) != vehicle_value:
                    raise ValidationError({"vehicle": "Selected vehicle does not match registration."})
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
                make_model = str(data.get("vehicleInfo") or "").strip().split(maxsplit=1)
                vehicle = Vehicle.objects.create(
                    company=company, branch=branch, customer=customer,
                    registration=registration, make=make_model[0] if make_model else "",
                    model=make_model[1] if len(make_model) > 1 else "",
                    vin=str(data.get("vin") or "").strip(),
                )

            if chosen_customer and (
                chosen_customer.name != name or
                "".join(filter(str.isdigit, chosen_customer.phone)) != "".join(filter(str.isdigit, phone))
            ):
                raise ValidationError({
                    "customer": "Selected customer details changed. Select the customer again."
                })
            data["customer"] = str(customer.id)
            data["vehicle"] = str(vehicle.id)
            raw_km = data.get("kilometre")
            if raw_km not in (None, ""):
                try:
                    km = int(raw_km)
                except (TypeError, ValueError):
                    raise ValidationError({"kilometre": "Enter a valid non-negative odometer reading."})
                if km < 0:
                    raise ValidationError({"kilometre": "Odometer reading cannot be negative."})
                data["odometer"] = km
            if "fuelLevel" in data:
                data["fuel_level"] = data["fuelLevel"]

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
        count=Job.objects.filter(company=company).count()+1
        number=serializer.validated_data.get("job_number") or f"JOB-{timezone.now().year}-{count:05d}"
        job=serializer.save(company=company,branch=self.request.user.branch,job_number=number,advisor=self.request.user)
        JobActivity.objects.create(company=company,branch=self.request.user.branch,job=job,event="Job Created",description=f"{number} created",actor=self.request.user,to_status=job.status)

    @decorators.action(detail=True,methods=["patch"])
    def status(self,request,pk=None):
        job=self.get_object(); new_status=request.data.get("status")
        if new_status not in FLOW: raise ValidationError({"status":"Invalid job status."})
        if stage_index_from_status(new_status) > STAGES.index(workflow_state(job)["current"]):
            raise ValidationError({"status":"Complete the current Job Card stage first."})
        old=job.status
        self._guard_active_timers(job,new_status)
        if new_status==Job.STATUS_DELIVERED: job.delivered_at=timezone.now()
        job.status=new_status; job.save(update_fields=["status","delivered_at","updated_at"])
        JobActivity.objects.create(company=job.company,branch=job.branch,job=job,event="Status Changed",description=f"{old} → {new_status}",actor=request.user,from_status=old,to_status=new_status)
        return response.Response(JobSerializer(job).data)

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
        if stage != state["current"]:
            raise ValidationError({"stage": f"Complete {state['current']} before {stage}."})

        if stage == "overview":
            if not job.customer_id or not job.vehicle_id:
                raise ValidationError({"stage":"Select a customer and vehicle first."})
        elif stage == "inspection":
            if (job.inspection or {}).get("status") != "Completed":
                raise ValidationError({"stage":"Finish the vehicle inspection checklist first."})
        elif stage == "estimate":
            estimate = job.estimates.order_by("-created_at").first()
            if not estimate:
                raise ValidationError({"stage":"Create an estimate before approval."})
            if estimate.status == "Rejected":
                raise ValidationError({"stage":"Create a revised estimate after customer rejection."})
            if (job.inspection or {}).get("revisedAt"):
                revised = parse_datetime(job.inspection["revisedAt"])
                if revised and estimate.created_at < revised:
                    raise ValidationError({"stage": "Inspection changed. Save a revised estimate before approval."})
            if not request.data.get("approveEstimate"):
                raise ValidationError({"stage":"Confirm customer estimate approval to continue."})
            estimate.status = "Approved"
            estimate.approved_at = timezone.now()
            estimate.save(update_fields=["status","approved_at","updated_at"])
        elif stage == "work":
            if not request.data.get("confirmWorkDone"):
                raise ValidationError({"stage":"Confirm that workshop work is complete."})
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
            self._guard_active_timers(job, "Delivered")

        progress = dict(job.workflow_progress or {})
        completed = set(state["completed"])
        completed.add(stage)
        progress.update({
            "started": True,
            "completed": [name for name in STAGES if name in completed],
            "lastCompletedAt": timezone.now().isoformat(),
        })
        old_status = job.status
        job.workflow_progress = progress
        job.status = NEXT_STATUS[stage]
        if stage == "invoice":
            job.delivered_at = timezone.now()
        job.save(update_fields=["workflow_progress","status","delivered_at","updated_at"])
        JobActivity.objects.create(
            company=job.company,branch=job.branch,job=job,actor=request.user,
            event="Workflow Stage Completed",description=f"{stage} completed",
            from_status=old_status,to_status=job.status,
            metadata={"stage":stage},
        )
        return response.Response({
            **workflow_state(job),
            "job": JobSerializer(job).data,
        })

    @decorators.action(detail=True,methods=["get"],url_path="inspection")
    def inspection_detail(self,request,pk=None):
        job=self.get_object()
        return response.Response(job.inspection or {
            "status":"Not Started","checklist":{},"findings":[],
            "diagnosticScan":{"performed":False,"codes":[]},"photos":[],
            "summary":{"totalChecks":0,"good":0,"needsAttention":0,"issuesFound":0,"criticalIssues":0,"recommendedRepairs":0}
        })

    @decorators.action(detail=True,methods=["post"],url_path="inspection/start")
    def inspection_start(self,request,pk=None):
        job=self.get_object()
        if workflow_state(job)["current"] == "overview":
            raise ValidationError({"stage":"Complete Overview before starting Inspection."})
        data=dict(job.inspection or {})
        data.setdefault("checklist",{}); data.setdefault("findings",[]); data.setdefault("diagnosticScan",{"performed":False,"codes":[]}); data.setdefault("photos",[])
        data["status"]="In Progress"; data["startedAt"]=timezone.now().isoformat()
        job.inspection=data; job.status=Job.STATUS_INSPECTION; job.save(update_fields=["inspection","status","updated_at"])
        return response.Response(data)

    @decorators.action(detail=True,methods=["patch"],url_path="inspection/checklist")
    def inspection_checklist(self,request,pk=None):
        job=self.get_object()
        self._assert_inspection_editable(job)
        item = str(request.data.get("itemName") or "").strip()
        check_status = request.data.get("status", "Not Checked")
        if not item or len(item) > 120 or check_status not in {"Good", "Needs Attention", "Critical", "Not Checked"}:
            raise ValidationError({"checklist": "Select a valid inspection item and status."})
        data=dict(job.inspection or {}); checklist=dict(data.get("checklist") or {})
        previous = checklist.get(item,"Not Checked")
        checklist[item] = check_status
        data["checklist"]=checklist; job.inspection=data; job.save(update_fields=["inspection","updated_at"])
        if previous != check_status: self._audit_inspection_change(job, f"{item} updated", previous, check_status)
        return response.Response(job.inspection)

    @decorators.action(detail=True,methods=["post"],url_path="inspection/findings")
    def inspection_findings(self,request,pk=None):
        import uuid
        job=self.get_object()
        self._assert_inspection_editable(job)
        data=dict(job.inspection or {}); findings=list(data.get("findings") or [])
        finding={"id":f"FND-{uuid.uuid4().hex[:10].upper()}","dateTime":timezone.now().isoformat(),"addedToEstimate":False,"estimateStatus":"Not Added",**request.data}
        findings.insert(0,finding); data["findings"]=findings; job.inspection=data; job.save(update_fields=["inspection","updated_at"])
        self._audit_inspection_change(job, "Finding added", None, finding)
        return response.Response(finding,status=status.HTTP_201_CREATED)

    @decorators.action(detail=True,methods=["put","delete"],url_path=r"inspection/findings/(?P<finding_id>[^/.]+)")
    def inspection_finding_detail(self,request,pk=None,finding_id=None):
        job=self.get_object()
        self._assert_inspection_editable(job)
        data=dict(job.inspection or {}); findings=list(data.get("findings") or [])
        idx=next((i for i,x in enumerate(findings) if str(x.get("id"))==str(finding_id)),None)
        if idx is None: return response.Response({"message":"Finding not found."},status=404)
        if request.method=="DELETE":
            removed=findings.pop(idx); data["findings"]=findings; job.inspection=data; job.save(update_fields=["inspection","updated_at"])
            self._audit_inspection_change(job, "Finding removed", removed, None)
            return response.Response(removed)
        old_finding = dict(findings[idx])
        findings[idx]={**findings[idx],**request.data}; data["findings"]=findings; job.inspection=data; job.save(update_fields=["inspection","updated_at"])
        self._audit_inspection_change(job, "Finding edited", old_finding, findings[idx])
        return response.Response(findings[idx])

    @decorators.action(detail=True,methods=["post"],url_path="inspection/diagnostic")
    def inspection_diagnostic(self,request,pk=None):
        import uuid
        job=self.get_object()
        self._assert_inspection_editable(job)
        data=dict(job.inspection or {}); scan=dict(data.get("diagnosticScan") or {})
        codes=list(scan.get("codes") or []); code={"id":f"OBD-{uuid.uuid4().hex[:8].upper()}",**request.data}
        codes.append(code); scan.update({"performed":True,"scanTime":timezone.now().isoformat(),"codes":codes}); data["diagnosticScan"]=scan
        job.inspection=data; job.save(update_fields=["inspection","updated_at"])
        self._audit_inspection_change(job, "Diagnostic code added", None, code)
        return response.Response(code,status=201)

    @decorators.action(detail=True,methods=["post"],url_path="inspection/photos")
    def inspection_photo(self,request,pk=None):
        import uuid
        job=self.get_object()
        self._assert_inspection_editable(job)
        data=dict(job.inspection or {}); photos=list(data.get("photos") or [])
        photo={"id":f"PH-{uuid.uuid4().hex[:8].upper()}","dateTime":timezone.now().isoformat(),**request.data}
        photos.append(photo); data["photos"]=photos; job.inspection=data; job.save(update_fields=["inspection","updated_at"])
        self._audit_inspection_change(job, "Inspection photo added", None, {"id": photo["id"], "caption": photo.get("caption")})
        return response.Response(photo,status=201)

    @decorators.action(detail=True,methods=["post"],url_path="inspection/complete")
    def inspection_complete(self,request,pk=None):
        job=self.get_object(); data=dict(job.inspection or {})
        if data.get("status") == "Completed":
            raise ValidationError({"inspection": "Inspection is already completed. Use Edit Inspection before estimate approval."})
        self._assert_inspection_editable(job)
        checked = data.get("checklist") or {}
        if not any(value in {"Good","Needs Attention","Critical"} for value in checked.values()):
            raise ValidationError({"inspection":"Record inspection checks before completing."})
        data["status"]="Completed"; data["completedAt"]=timezone.now().isoformat()
        job.inspection=data; job.status=Job.STATUS_ESTIMATE_PENDING if request.data.get("needsApproval",True) else job.status
        job.save(update_fields=["inspection","status","updated_at"])
        return response.Response({"inspection":data,"newJobStatus":job.status})

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
        job = self.get_object()
        if request.data.get("decision") != "Rejected":
            raise ValidationError({"decision":"Use Complete & Continue to approve an estimate."})
        estimate = job.estimates.filter(pk=estimate_id).first()
        if not estimate:
            raise ValidationError({"estimate":"Estimate not found for this Job Card."})
        estimate.status = "Rejected"
        estimate.save(update_fields=["status","updated_at"])
        return response.Response(JobEstimateSerializer(estimate).data)

    def _quick_estimate_values(self, data):
        lines = data.get("lines")
        if not isinstance(lines, list) or not 1 <= len(lines) <= 75:
            raise ValidationError({"lines": "Add 1 to 75 work or part items."})
        try:
            tax_rate = Decimal(str(data.get("taxPercent", 0)))
            discount = Decimal(str(data.get("discount", 0)))
        except (TypeError, ValueError, ArithmeticError):
            raise ValidationError({"total": "Tax and discount must be valid numbers."})
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
            if not (Decimal("0") < quantity <= Decimal("10000")) or not (Decimal("0") <= price <= Decimal("10000000")):
                raise ValidationError({"lines": "Enter a positive quantity and non-negative price."})
            quantity = quantity.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            price = price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total = (quantity * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            subtotal += total
            item_type = str(row.get("type") or "Service")[:30]
            catalog_id = str(row.get("catalogId") or "").strip()
            line = {
                "description": label,
                "type": item_type,
                "quantity": str(quantity), "unitPrice": str(price), "total": str(total),
            }
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
        tax = ((subtotal - discount) * tax_rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        grand = subtotal - discount + tax
        items.append({"_pricing": {"taxPercent": str(tax_rate), "discount": str(discount)}})
        return {"items": items, "subtotal": subtotal, "tax": tax, "total": grand}

    @decorators.action(detail=True, methods=["post"], url_path="estimates/quick")
    @transaction.atomic
    def quick_estimate(self, request, pk=None):
        job = Job.objects.select_for_update().get(pk=self.get_object().pk, company=request.user.company)
        if workflow_state(job)["current"] != "estimate":
            raise ValidationError({"estimate": "Only the active Estimate stage can create a quotation."})
        values = self._quick_estimate_values(request.data)
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
        job = self.get_object()
        if workflow_state(job)["current"] != "estimate":
            raise ValidationError({"estimate": "Estimate editing is closed after approval."})
        draft = JobEstimate.objects.select_for_update().filter(
            pk=estimate_id, job=job, company=job.company,
        ).first()
        if not draft:
            raise ValidationError({"estimate": "Estimate not found for this Job Card."})
        if draft.status != "Draft":
            raise ValidationError({"estimate": "Only draft estimates can be edited."})
        values = self._quick_estimate_values(request.data)
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
            s=JobEstimateSerializer(data=request.data); s.is_valid(raise_exception=True)
            obj=s.save(company=job.company,branch=job.branch,job=job)
            return response.Response(JobEstimateSerializer(obj).data,status=status.HTTP_201_CREATED)
        return response.Response(JobEstimateSerializer(job.estimates.all(),many=True).data)

    @decorators.action(detail=True,methods=["get","post"])
    def photos(self,request,pk=None):
        job=self.get_object()
        if request.method=="POST":
            s=JobPhotoSerializer(data=request.data); s.is_valid(raise_exception=True)
            obj=s.save(company=job.company,branch=job.branch,job=job,uploaded_by=request.user)
            return response.Response(JobPhotoSerializer(obj).data,status=status.HTTP_201_CREATED)
        return response.Response(JobPhotoSerializer(job.photos.all(),many=True).data)

    @decorators.action(detail=True,methods=["get"])
    def activity(self,request,pk=None):
        return response.Response(JobActivitySerializer(self.get_object().activities.all(),many=True).data)

    @decorators.action(detail=True,methods=["post"],url_path="issue-part")
    @transaction.atomic
    def issue_part(self,request,pk=None):
        from apps.inventory.models import StockItem,StockMovement
        job=self.get_object(); item=StockItem.objects.select_for_update().get(pk=request.data.get("item"),company=job.company)
        qty=Decimal(str(request.data.get("quantity",0)))
        if qty<=0 or item.available_quantity<qty: raise ValidationError("Insufficient stock.")
        item.on_hand=item.on_hand-qty; item.save(update_fields=["on_hand","updated_at"])
        part=JobPart.objects.create(company=job.company,branch=job.branch,job=job,item=item,quantity=qty,unit_price=item.selling_price,issued=True)
        StockMovement.objects.create(company=job.company,branch=job.branch,item=item,movement_type="job_issue",quantity=-qty,reference=job.job_number,job=job,created_by=request.user)
        return response.Response(JobPartSerializer(part).data,status=status.HTTP_201_CREATED)
