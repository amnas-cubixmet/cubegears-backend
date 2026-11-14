"""Tenant-scoped branch membership for customer workflows.

Company owner is authorized across active company branches. Staff are
authorized only for their primary branch and up to two additional branches
explicitly assigned by an administrator. Every API must re-check access.
"""
from apps.accounts.permissions import is_company_owner
from apps.branches.models import Branch


def authorized_branch_ids(user):
    if not getattr(user, "is_authenticated", False) or not user.company_id:
        return set()
    if is_company_owner(user):
        return set(Branch.objects.filter(
            company_id=user.company_id, is_active=True
        ).values_list("id", flat=True))
    ids = set(Branch.objects.filter(
        pk__in=user.allowed_branches.values_list("id", flat=True),
        company_id=user.company_id, is_active=True,
    ).values_list("id", flat=True))
    if user.branch_id:
        primary = Branch.objects.filter(
            pk=user.branch_id, company_id=user.company_id, is_active=True,
        ).values_list("pk", flat=True).first()
        if primary:
            ids.add(primary)
    return ids


def default_authorized_branch(user):
    ids = authorized_branch_ids(user)
    if user.branch_id in ids:
        return Branch.objects.filter(pk=user.branch_id).first()
    if not ids:
        return None
    return Branch.objects.filter(
        pk__in=ids,
    ).order_by("-is_head_office", "name", "pk").first()
