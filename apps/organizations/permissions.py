"""Role checks shared by admin, API and services.

All authorization questions of the form "may this user do X in this
organization?" go through these helpers so admin and API agree.
"""

from __future__ import annotations

from .models import Membership, Organization

Role = Membership.Role


def role_for(user, organization) -> str | None:
    """The user's active role in `organization`, or None."""
    if not user or not user.is_authenticated or organization is None:
        return None
    org_id = organization.pk if isinstance(organization, Organization) else organization
    return (
        Membership.objects.filter(user=user, organization_id=org_id, is_active=True)
        .values_list("role", flat=True)
        .first()
    )


def has_role(user, organization, minimum: str) -> bool:
    if user and user.is_authenticated and user.is_superuser:
        return True
    role = role_for(user, organization)
    return role is not None and role in Membership.roles_at_least(minimum)


def can_view(user, organization) -> bool:
    return has_role(user, organization, Role.VIEWER)


def can_edit(user, organization) -> bool:
    return has_role(user, organization, Role.STAFF)


def can_admin(user, organization) -> bool:
    return has_role(user, organization, Role.ADMIN)


def organization_ids_for(user, minimum: str = Role.VIEWER) -> list[int]:
    """Org ids where `user` has at least `minimum`. Superusers: every org."""
    if not user or not user.is_authenticated:
        return []
    if user.is_superuser:
        return list(Organization.objects.values_list("pk", flat=True))
    return list(
        Membership.objects.filter(user=user, is_active=True, role__in=Membership.roles_at_least(minimum)).values_list(
            "organization_id", flat=True
        )
    )
