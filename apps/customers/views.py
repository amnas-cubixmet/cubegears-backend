import uuid
from django.db import transaction
from django.db.models import Q, Sum
from rest_framework import decorators, response, status
from common.viewsets import CompanyScopedModelViewSet
from .models import Customer, CustomerActivity, CustomerReminder
from .serializers import CustomerSerializer, CustomerActivitySerializer, CustomerReminderSerializer
from apps.branches.models import Branch

class CustomerViewSet(CompanyScopedModelViewSet):
    queryset = Customer.objects.select_related("branch").all()
    serializer_class = CustomerSerializer
    action_permission_map = {
        "check_duplicate": "customers.view",
        "archive": "customers.edit",
        "vehicles": {"GET": "vehicles.view", "POST": "vehicles.create"},
        "jobs": "jobs.view",
        "invoices": "invoices.view",
        "payments": "payments.view",
        "activity": "customers.view",
        "outstanding": "invoices.view",
        "branch_options": "customers.view",
        "restore": "customers.edit",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.query_params.get("search")
        if q:
            qs = qs.filter(Q(name__icontains=q)|Q(phone__icontains=q)|Q(whatsapp__icontains=q)|Q(email__icontains=q)|Q(company_name__icontains=q))
        st = self.request.query_params.get("status")
        if st and st.lower() != "all": qs = qs.filter(status__iexact=st)
        ctype = self.request.query_params.get("customerType")
        if ctype and ctype.lower() != "all": qs = qs.filter(customer_type__iexact=ctype)
        branch = self.request.query_params.get("branch")
        if branch and branch.lower() != "all":
            try:
                branch_id = uuid.UUID(branch)
            except (ValueError, AttributeError, TypeError):
                return qs.none()
            qs = qs.filter(branch_id=branch_id)
        return qs

    def perform_create(self, serializer):
        # The base view assigns the user's default branch; honor a selected
        # branch only after the base create() validates its tenant ownership.
        selected = serializer.validated_data.get("branch")
        branch = selected if selected is not None else self.request.user.branch
        if branch is not None and not branch.is_active:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"branch": "Select an active workshop branch."})
        serializer.save(company=self.request.user.company, branch=branch)

    @decorators.action(detail=False, methods=["get"], url_path="branch-options")
    def branch_options(self, request):
        branches = Branch.objects.filter(
            company=request.user.company, is_active=True
        ).order_by("name", "pk")
        return response.Response([
            {"id": str(item.pk), "name": item.name} for item in branches
        ])

    @decorators.action(detail=False, methods=["post"], url_path="check-duplicate")
    def check_duplicate(self, request):
        phone = str(request.data.get("phone") or "").strip()
        email = str(request.data.get("email") or "").strip()
        # Duplicate detection must be based on ALL records in this tenant,
        # not user-supplied list filters like status/search/branch.
        qs = Customer.objects.filter(company_id=request.user.company_id)
        if phone or email:
            match_filter = Q(phone=phone) if phone else Q(pk__isnull=True)
            if email:
                match_filter |= Q(email__iexact=email)
            match = qs.filter(match_filter).first()
        else:
            match = None
        return response.Response({"duplicate": bool(match), "customer": CustomerSerializer(match).data if match else None})

    def _set_archived(self, request, customer, archived):
        previous = customer.status
        new_status = "archived" if archived else "active"
        if previous != new_status:
            customer.status = new_status
            customer.save(update_fields=["status", "updated_at"])
            CustomerActivity.objects.create(
                company=customer.company, branch=customer.branch, customer=customer,
                activity_type="Customer Archived" if archived else "Customer Restored",
                description="Customer status changed without deleting historical records.",
                actor=request.user,
                metadata={"previous": previous, "status": new_status},
            )
        return customer

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        # DELETE is intentionally non-destructive. Preserve jobs, invoices,
        # vehicles, payments, photo references and customer portal audit.
        customer = self.get_object()
        self._set_archived(request, customer, True)
        return response.Response(status=status.HTTP_204_NO_CONTENT)

    @transaction.atomic
    @decorators.action(detail=True, methods=["patch"])
    def archive(self, request, pk=None):
        customer = self._set_archived(request, self.get_object(), True)
        return response.Response(self.get_serializer(customer).data)

    @transaction.atomic
    @decorators.action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        customer = self._set_archived(request, self.get_object(), False)
        return response.Response(self.get_serializer(customer).data)

    @decorators.action(detail=True, methods=["get","post"])
    def vehicles(self, request, pk=None):
        from apps.vehicles.models import Vehicle
        from apps.vehicles.serializers import VehicleSerializer
        customer=self.get_object()
        if request.method == "POST":
            if customer.status == "archived":
                from rest_framework.exceptions import ValidationError
                raise ValidationError({"customer": "Restore the archived customer before adding a vehicle."})
            s = VehicleSerializer(
                data={**request.data, "customer": str(customer.id)},
                context={"request": request},
            )
            s.is_valid(raise_exception=True)
            branch = customer.branch or request.user.branch
            if branch and not branch.is_active:
                from rest_framework.exceptions import ValidationError
                raise ValidationError({"branch": "The customer branch is inactive."})
            vehicle = s.save(company=request.user.company, branch=branch, customer=customer)
            return response.Response(self._vehicle_response(vehicle, request), status=status.HTTP_201_CREATED)
        vehicles = Vehicle.objects.filter(company_id=request.user.company_id, customer=customer)
        return response.Response(VehicleSerializer(vehicles, many=True, context={"request": request}).data)

    def _vehicle_response(self, vehicle, request):
        from apps.vehicles.serializers import VehicleSerializer
        return VehicleSerializer(vehicle, context={"request": request}).data

    @decorators.action(detail=True, methods=["get"])
    def jobs(self, request, pk=None):
        from apps.jobs.models import Job
        from apps.jobs.serializers import JobSerializer
        return response.Response(JobSerializer(Job.objects.filter(customer=self.get_object()).order_by("-created_at"),many=True).data)

    @decorators.action(detail=True, methods=["get"])
    def invoices(self, request, pk=None):
        from apps.invoices.models import Invoice
        from apps.invoices.serializers import InvoiceSerializer
        return response.Response(InvoiceSerializer(Invoice.objects.filter(customer=self.get_object()).order_by("-date"),many=True).data)

    @decorators.action(detail=True, methods=["get"])
    def payments(self, request, pk=None):
        from apps.payments.models import Payment
        from apps.payments.serializers import PaymentSerializer
        return response.Response(PaymentSerializer(Payment.objects.filter(customer=self.get_object()).order_by("-date"),many=True).data)

    @decorators.action(detail=True, methods=["get"])
    def activity(self, request, pk=None):
        return response.Response(CustomerActivitySerializer(CustomerActivity.objects.filter(customer=self.get_object()),many=True).data)

    @decorators.action(detail=True, methods=["get"])
    def outstanding(self, request, pk=None):
        from apps.invoices.models import Invoice
        total=Invoice.objects.filter(customer=self.get_object()).aggregate(total=Sum("balance"))["total"] or 0
        return response.Response({"customerId":pk,"outstanding":total})

class CustomerReminderViewSet(CompanyScopedModelViewSet):
    queryset=CustomerReminder.objects.all()
    serializer_class=CustomerReminderSerializer
