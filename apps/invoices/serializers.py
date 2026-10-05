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

    def to_internal_value(self, data):
        known = {"id", "invoiceId", "number", "status", "vehicle_no", "transporter_name", "transporter_id", "distance_km", "payload"}
        raw = dict(data)
        payload = dict(raw.get("payload") or {})
        for key, value in raw.items():
            if key not in known:
                payload[key] = value

        transport = raw.get("transport") or payload.get("transport") or {}
        raw["vehicle_no"] = raw.get("vehicle_no") or transport.get("vehicleNo") or ""
        raw["transporter_name"] = raw.get("transporter_name") or transport.get("transporterName") or ""
        raw["transporter_id"] = raw.get("transporter_id") or transport.get("transporterId") or ""
        try:
            raw["distance_km"] = int(raw.get("distance_km") or transport.get("distanceKm") or 0)
        except (TypeError, ValueError):
            raw["distance_km"] = 0
        raw["payload"] = payload
        filtered = {k: v for k, v in raw.items() if k in known}
        return super().to_internal_value(filtered)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        merged = dict(instance.payload or {})
        merged.update({
            "id": str(instance.id),
            "invoiceId": str(instance.invoice_id) if instance.invoice_id else "",
            "status": instance.status,
            "ewayBillNo": instance.number or merged.get("ewayBillNo", ""),
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
