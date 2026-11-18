from uuid import uuid4

from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.branch_access import authorized_branch_ids
from apps.accounts.permissions import is_company_owner, user_has_permission
from apps.customers.serializers import CustomerSerializer
from apps.vehicles.serializers import VehicleSerializer


JOB_FIELDS = {"serviceType", "odometer", "fuel_level", "priority", "notes", "technician", "complaints"}
CUSTOMER_FIELDS = {"name", "phone", "email", "address"}
VEHICLE_FIELDS = {"registration", "make", "model", "fuelType", "vin"}
COMPLAINT_FIELDS = {"description", "wording", "category", "priority", "inspectionNotes"}


def editor_profile_access(user, job):
    customer_branch_access = is_company_owner(user) or job.customer.branch_id in authorized_branch_ids(user)
    return {
        "customer": (job.customer.company_id == job.company_id and customer_branch_access
                     and user_has_permission(user, "customers.edit")),
        "vehicle": job.vehicle.company_id == job.company_id and user_has_permission(user, "vehicles.edit"),
    }


def editor_payload(view, job):
    access = editor_profile_access(view.request.user, job)
    return {
        "job": view.get_serializer(job).data,
        "customer": {"name": job.customer.name, "phone": job.customer.phone,
                     "email": job.customer.email if access["customer"] else "",
                     "address": job.customer.address if access["customer"] else ""},
        "vehicle": {"registration": job.vehicle.registration, "make": job.vehicle.make,
                    "model": job.vehicle.model, "fuelType": job.vehicle.fuel_type, "vin": job.vehicle.vin},
        "access": access,
        "versions": {"job": job.updated_at.isoformat(), "customer": job.customer.updated_at.isoformat(),
                     "vehicle": job.vehicle.updated_at.isoformat()},
    }


def editor_changes(data, field, allowed):
    changes = data.get(field, {})
    if not isinstance(changes, dict) or set(changes) - allowed:
        raise ValidationError({field: "This edit contains unsupported fields."})
    return changes


def editor_profile_serializer(request, job, kind, changes, access):
    if not changes:
        return None
    if not access[kind]:
        raise PermissionDenied(f"You do not have permission to edit the linked {kind} profile.")
    original = getattr(job, kind)
    locked = type(original).objects.select_for_update().get(pk=original.pk, company_id=job.company_id)
    if (kind == "customer" and not is_company_owner(request.user)
            and locked.branch_id not in authorized_branch_ids(request.user)):
        raise PermissionDenied("You do not have permission to edit the linked customer profile.")
    serializer_class = CustomerSerializer if kind == "customer" else VehicleSerializer
    serializer = serializer_class(locked, data=changes, partial=True, context={"request": request})
    serializer.is_valid(raise_exception=True)
    return serializer


def editor_complaints(job, rows):
    existing = job.complaints or []
    if not isinstance(rows, list) or not len(existing) <= len(rows) <= 200:
        raise ValidationError({"complaints": "Keep saved complaints and enter up to 200 complaints."})
    complaints = []
    for index, row in enumerate(rows):
        stored = existing[index] if index < len(existing) else {}
        if (not isinstance(row, dict) or not isinstance(stored, dict)
                or (index < len(existing) and stored.get("id") != row.get("id"))):
            raise ValidationError({"complaints": "The complaint list changed. Reload the Job Card and try again."})
        allowed = {key: row[key] for key in COMPLAINT_FIELDS if key in row}
        if any(not isinstance(value, str) for value in allowed.values()):
            raise ValidationError({"complaints": "Enter valid text for each complaint."})
        if index < len(existing) and not allowed:
            complaints.append(stored)
            continue
        item = {**stored, **allowed}
        description = (item.get("description") or item.get("wording") or "").strip()
        if not 1 <= len(description) <= 2000:
            raise ValidationError({"complaints": f"Enter a description of up to 2000 characters for complaint {index + 1}."})
        item.update({"description": description, "wording": description})
        if index >= len(existing):
            item["id"] = str(uuid4())
        complaints.append(item)
    return complaints
