from django.db.models import Q, Sum
from rest_framework import decorators, response, status
from common.viewsets import CompanyScopedModelViewSet
from .models import Customer, CustomerActivity, CustomerReminder
from .serializers import CustomerSerializer, CustomerActivitySerializer, CustomerReminderSerializer

class CustomerViewSet(CompanyScopedModelViewSet):
    queryset = Customer.objects.select_related("branch").all()
    serializer_class = CustomerSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.query_params.get("search")
        if q:
            qs = qs.filter(Q(name__icontains=q)|Q(phone__icontains=q)|Q(whatsapp__icontains=q)|Q(email__icontains=q)|Q(company_name__icontains=q))
        st = self.request.query_params.get("status")
        if st and st.lower() != "all": qs = qs.filter(status__iexact=st)
        ctype = self.request.query_params.get("customerType")
        if ctype and ctype.lower() != "all": qs = qs.filter(customer_type__iexact=ctype)
        return qs

    @decorators.action(detail=False, methods=["post"], url_path="check-duplicate")
    def check_duplicate(self, request):
        phone, email = request.data.get("phone"), request.data.get("email")
        qs = self.get_queryset()
        match = qs.filter(Q(phone=phone) | Q(email__iexact=email)).first() if phone or email else None
        return response.Response({"duplicate": bool(match), "customer": CustomerSerializer(match).data if match else None})

    @decorators.action(detail=True, methods=["patch"])
    def archive(self, request, pk=None):
        obj=self.get_object(); obj.status="archived"; obj.save(update_fields=["status","updated_at"])
        return response.Response(CustomerSerializer(obj).data)

    @decorators.action(detail=True, methods=["get","post"])
    def vehicles(self, request, pk=None):
        from apps.vehicles.models import Vehicle
        from apps.vehicles.serializers import VehicleSerializer
        customer=self.get_object()
        if request.method=="POST":
            s=VehicleSerializer(data={**request.data,"customer":str(customer.id)})
            s.is_valid(raise_exception=True)
            vehicle=s.save(company=request.user.company, branch=request.user.branch, customer=customer)
            return response.Response(VehicleSerializer(vehicle).data,status=status.HTTP_201_CREATED)
        return response.Response(VehicleSerializer(Vehicle.objects.filter(customer=customer),many=True).data)

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
