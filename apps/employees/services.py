from django.db.models import Q

from .models import Employee


def _next_employee_code(company):
    used=set(
        Employee.objects.filter(company=company)
        .values_list("employee_code",flat=True)
    )
    number=1
    while number<100000:
        code=f"EMP-{number:04d}"
        if code not in used:
            return code
        number+=1
    raise RuntimeError("Unable to allocate employee code.")


def resolve_employee_for_user(user, create_owner=True):
    """
    Resolve the Employee row linked to an authenticated tenant user.

    Existing employee records are linked by exact company + email/phone when
    safe. Tenant SUPER_ADMIN owners are provisioned automatically so My
    Attendance works for accounts created before employee auto-provisioning
    was introduced.
    """
    if not user or not getattr(user,"is_authenticated",False):
        return None

    try:
        employee=user.employee_profile
    except Exception:
        employee=None

    if employee:
        return employee

    company=getattr(user,"company",None)
    if not company:
        return None

    candidates=Employee.objects.filter(
        company=company,
        user__isnull=True,
    )

    identity=Q()
    if getattr(user,"email",""):
        identity|=Q(email__iexact=user.email.strip())
    if getattr(user,"phone",""):
        identity|=Q(phone=user.phone.strip())

    if identity:
        matches=list(candidates.filter(identity)[:2])
        if len(matches)==1:
            employee=matches[0]
            employee.user=user
            update_fields=["user","updated_at"]
            if not employee.branch_id and getattr(user,"branch_id",None):
                employee.branch=user.branch
                update_fields.append("branch")
            employee.save(update_fields=update_fields)
            return employee

    role=getattr(user,"role",None)
    if not create_owner or getattr(role,"code","")!="SUPER_ADMIN":
        return None

    return Employee.objects.create(
        company=company,
        branch=getattr(user,"branch",None),
        user=user,
        employee_code=_next_employee_code(company),
        name=(getattr(user,"name","") or getattr(user,"email","") or "Workshop Owner").strip(),
        phone=(getattr(user,"phone","") or "").strip(),
        email=(getattr(user,"email","") or "").strip(),
        designation="Owner / Super Admin",
        role_name="Super Admin",
        employment_type="Full Time",
        status="Active",
    )
