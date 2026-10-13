from rest_framework import serializers
from apps.customers.models import Customer
from apps.vehicles.models import Vehicle
from apps.jobs.models import Job
from apps.inventory.models import StockItem
from .models import Invoice, InvoiceItem, EWayBill

class InvoiceItemSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source="item_type", required=False)
    hsnCode = serializers.CharField(source="hsn_sac", required=False, allow_blank=True)
    qty = serializers.DecimalField(source="quantity", max_digits=12, decimal_places=2, required=False)
    purchasePrice = serializers.DecimalField(source="purchase_price", max_digits=12, decimal_places=2, required=False)
    taxRate = serializers.DecimalField(source="tax_rate", max_digits=5, decimal_places=2, required=False)
    inventoryId = serializers.UUIDField(source="stock_item_id", required=False, allow_null=True)

    class Meta:
        model = InvoiceItem
        exclude = ("invoice",)

class InvoiceSerializer(serializers.ModelSerializer):
    number = serializers.CharField(required=False)
    invoiceType = serializers.CharField(source="invoice_type", required=False)
    items = InvoiceItemSerializer(many=True, required=False)
    customer = serializers.JSONField(write_only=True, required=False)
    vehicle = serializers.JSONField(write_only=True, required=False)
    jobCardNo = serializers.CharField(write_only=True, required=False, allow_blank=True)
    seller = serializers.JSONField(source="seller_snapshot", required=False)
    transportation = serializers.JSONField(source="transport", required=False)
    taxMode = serializers.CharField(source="tax_mode", required=False)
    cgstRate = serializers.DecimalField(source="cgst_rate", max_digits=5, decimal_places=2, required=False)
    sgstRate = serializers.DecimalField(source="sgst_rate", max_digits=5, decimal_places=2, required=False)
    igstRate = serializers.DecimalField(source="igst_rate", max_digits=5, decimal_places=2, required=False)
    paymentType = serializers.CharField(source="payment_type", required=False, allow_blank=True)
    termsAndConditions = serializers.CharField(source="terms_conditions", required=False, allow_blank=True)
    staff = serializers.CharField(source="staff_name", required=False, allow_blank=True)
    sourceJobId = serializers.CharField(source="source_job_id", required=False, allow_blank=True)
    customerId = serializers.UUIDField(source="customer_id", read_only=True)
    customerName = serializers.CharField(source="customer.name", read_only=True)
    vehicleId = serializers.UUIDField(source="vehicle_id", read_only=True)
    paymentMode = serializers.CharField(source="payment_mode", required=False, allow_blank=True)
    paymentTerms = serializers.CharField(source="payment_terms", required=False, allow_blank=True)

    class Meta:
        model = Invoice
        exclude = ("company", "branch")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        invoice_type = attrs.get("invoice_type") or (
            self.instance.invoice_type if self.instance else "regular"
        )
        rows = attrs.get("items")
        if invoice_type == "gst" and rows is not None:
            for index, row in enumerate(rows, 1):
                if "tax_rate" not in row:
                    raise serializers.ValidationError({
                        "items": f"Line {index} must specify the classified GST percentage (0% only if legally applicable)."
                    })
        return attrs

    def _company(self, validated_data):
        company = validated_data.get("company")
        if company:
            return company
        request = self.context.get("request")
        return getattr(getattr(request, "user", None), "company", None)

    def _resolve_customer(self, value, company):
        if value in (None, "", {}):
            return None
        if isinstance(value, str):
            try:
                return Customer.objects.get(pk=value, company=company)
            except (Customer.DoesNotExist, ValueError):
                return None

        phone = str(value.get("phone") or "").strip()
        email = str(value.get("email") or "").strip()
        name = str(value.get("name") or "Walk-in Customer").strip()
        qs = Customer.objects.filter(company=company)
        customer = None
        if phone:
            customer = qs.filter(phone=phone).first()
        if not customer and email:
            customer = qs.filter(email__iexact=email).first()
        if not customer:
            customer = Customer.objects.create(
                company=company,
                branch=getattr(getattr(self.context.get("request"), "user", None), "branch", None),
                name=name,
                phone=phone or "NA",
                email=email,
                gstin=value.get("gstin") or "",
                address=value.get("address") or "",
                state=value.get("state") or "",
            )
        return customer

    def _resolve_vehicle(self, value, company, customer):
        if value in (None, "", {}):
            return None
        if isinstance(value, str):
            try:
                return Vehicle.objects.get(pk=value, company=company)
            except (Vehicle.DoesNotExist, ValueError):
                return None

        registration = str(
            value.get("registration")
            or value.get("regNo")
            or value.get("licensePlate")
            or ""
        ).strip()
        if not registration:
            return None
        vehicle = Vehicle.objects.filter(company=company, registration__iexact=registration).first()
        if vehicle:
            return vehicle
        if not customer:
            return None
        make_model = str(value.get("makeModel") or "").strip().split(" ", 1)
        return Vehicle.objects.create(
            company=company,
            branch=getattr(getattr(self.context.get("request"), "user", None), "branch", None),
            customer=customer,
            registration=registration,
            make=make_model[0] if make_model else "",
            model=make_model[1] if len(make_model) > 1 else "",
            vin=value.get("vin") or "",
        )

    def _resolve_job(self, job_number, company):
        if not job_number:
            return None
        return Job.objects.filter(company=company, job_number__iexact=job_number).first()

    def create(self, validated_data):
        items = validated_data.pop("items", [])
        customer_payload = validated_data.pop("customer", None)
        vehicle_payload = validated_data.pop("vehicle", None)
        job_number = validated_data.pop("jobCardNo", "")
        company = self._company(validated_data)

        job = self._resolve_job(job_number, company)
        customer = self._resolve_customer(customer_payload, company) or (job.customer if job else None)
        if not customer:
            raise serializers.ValidationError({"customer": "Customer is required."})
        vehicle = self._resolve_vehicle(vehicle_payload, company, customer) or (job.vehicle if job else None)

        validated_data["customer"] = customer
        validated_data["vehicle"] = vehicle
        validated_data["job"] = job

        if isinstance(customer_payload, dict):
            validated_data["customer_snapshot"] = customer_payload
        if isinstance(vehicle_payload, dict):
            validated_data["vehicle_snapshot"] = vehicle_payload

        obj = Invoice.objects.create(**validated_data)
        for row in items:
            InvoiceItem.objects.create(invoice=obj, **row)
        return obj

    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        customer_payload = validated_data.pop("customer", None)
        vehicle_payload = validated_data.pop("vehicle", None)
        job_number = validated_data.pop("jobCardNo", None)
        company = instance.company

        if customer_payload is not None:
            customer = self._resolve_customer(customer_payload, company)
            if customer:
                instance.customer = customer
                if isinstance(customer_payload, dict):
                    instance.customer_snapshot = customer_payload
        if vehicle_payload is not None:
            vehicle = self._resolve_vehicle(vehicle_payload, company, instance.customer)
            instance.vehicle = vehicle
            if isinstance(vehicle_payload, dict):
                instance.vehicle_snapshot = vehicle_payload
        if job_number is not None:
            instance.job = self._resolve_job(job_number, company)

        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.save()

        if items is not None:
            instance.items.all().delete()
            for row in items:
                InvoiceItem.objects.create(invoice=instance, **row)
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        customer = dict(instance.customer_snapshot or {})
        customer.setdefault("name", instance.customer.name)
        customer.setdefault("phone", instance.customer.phone)
        customer.setdefault("email", instance.customer.email)
        customer.setdefault("gstin", instance.customer.gstin)
        customer.setdefault("address", instance.customer.address)
        data["customer"] = customer

        if instance.vehicle:
            vehicle = dict(instance.vehicle_snapshot or {})
            vehicle.setdefault("registration", instance.vehicle.registration)
            vehicle.setdefault("makeModel", " ".join(filter(None, [instance.vehicle.make, instance.vehicle.model])))
            vehicle.setdefault("vin", instance.vehicle.vin)
            vehicle.setdefault("odometer", instance.vehicle.odometer)
            data["vehicle"] = vehicle
        else:
            data["vehicle"] = {}

        data["jobCardNo"] = instance.job.job_number if instance.job else ""
        data["seller"] = instance.seller_snapshot or {}
        data["transportation"] = instance.transport or {}
        data["taxMode"] = instance.tax_mode
        data["cgstRate"] = instance.cgst_rate
        data["sgstRate"] = instance.sgst_rate
        data["igstRate"] = instance.igst_rate
        data["paymentType"] = instance.payment_type
        data["termsAndConditions"] = instance.terms_conditions
        data["staff"] = instance.staff_name
        data["sourceJobId"] = instance.source_job_id
        return data

class EWayBillSerializer(serializers.ModelSerializer):
    invoiceId = serializers.UUIDField(source="invoice_id", required=False, allow_null=True)

    class Meta:
        model = EWayBill
        exclude = ("company", "branch", "invoice")
        read_only_fields = ("id", "number", "status", "created_at", "updated_at")

    def to_internal_value(self, data):
        from .eway_validation import strip_official_fields
        if not isinstance(data, dict):
            raise serializers.ValidationError("E-Way Bill draft must be an object.")
        allowed = {"invoiceId", "vehicle_no", "transporter_name",
                   "transporter_id", "distance_km", "payload"}
        raw = {k: v for k, v in data.items() if k in allowed}
        payload = dict(raw.get("payload") or {})
        payload.update({k: v for k, v in data.items() if k not in allowed
                        and k not in {"number", "id", "status", "company", "branch"}})
        raw["payload"] = strip_official_fields(payload)
        transport = raw["payload"].get("transport") or {}
        if not isinstance(transport, dict):
            raise serializers.ValidationError({"transport": "Invalid transport object."})
        raw["vehicle_no"] = str(transport.get("vehicleNo") or raw.get("vehicle_no") or "")
        raw["transporter_name"] = str(transport.get("transporterName") or raw.get("transporter_name") or "")
        raw["transporter_id"] = str(transport.get("transporterId") or raw.get("transporter_id") or "")
        try:
            raw["distance_km"] = int(transport.get("distanceKm") or raw.get("distance_km") or 0)
        except (TypeError, ValueError):
            raise serializers.ValidationError({"distanceKm": "Distance must be a whole number."})
        return super().to_internal_value(raw)

    def validate(self, attrs):
        from .eway_validation import READONLY_PORTAL_FIELDS
        from .models import Invoice
        if self.instance and self.instance.status != "Draft":
            raise serializers.ValidationError({
                "status": "Only drafts can be edited. Reset a local API-ready draft first."
            })
        invoice_id = attrs.get("invoice_id")
        if invoice_id:
            company = self.instance.company if self.instance else getattr(
                self.context["request"].user, "company", None
            )
            if not Invoice.objects.filter(pk=invoice_id, company=company).exists():
                raise serializers.ValidationError({
                    "invoiceId": "Select an invoice belonging to your workshop."
                })
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        merged = dict(instance.payload or {})
        merged.update({
            "id": str(instance.id),
            "invoiceId": str(instance.invoice_id) if instance.invoice_id else "",
            "status": instance.status,
            "ewayBillNo": instance.number or "",
            "number": instance.number or "",
        })
        transport = dict(merged.get("transport") or {})
        transport.update({
            "vehicleNo": instance.vehicle_no or transport.get("vehicleNo", ""),
            "transporterName": instance.transporter_name or transport.get("transporterName", ""),
            "transporterId": instance.transporter_id or transport.get("transporterId", ""),
            "distanceKm": instance.distance_km or transport.get("distanceKm", ""),
        })
        merged["transport"] = transport
        return merged
