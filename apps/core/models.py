"""Shared model building blocks: timestamps, sharing visibility, and the
organization-scoping ("tenancy") contract every org-owned model follows.

Tenancy in one paragraph
------------------------
There is one shared database. Every org-owned model declares `ORG_PATH`, the
ORM lookup path from that model to its owning `organizations.Organization`
("organization" for top-level rows, "event__organization" for children of an
Event, and so on). `OrgScopedQuerySet` uses that one attribute to filter by
organization, by user membership, or by what another organization may see —
so admin, API and services all scope the same way (see core/admin.py and
api/permissions.py). Global, shared-directory models (Venue, Room,
SpeakerProfile, Company) deliberately have no ORG_PATH.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q


class TimeStampedModel(models.Model):
    """`created_at` / `updated_at` on every row that inherits it."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Visibility(models.TextChoices):
    """Who outside the owning organization can see a shareable row.

    PRIVATE — owning organization only.
    NETWORK — every participating organization on this installation
              (e.g. the shared upcoming-events calendar).
    PUBLIC  — anyone, including unauthenticated API clients.

    Cost/contract data never gets a Visibility field: it is always private
    (see events.EventContract).
    """

    PRIVATE = "private", "Private to my organization"
    NETWORK = "network", "Shared with participating organizations"
    PUBLIC = "public", "Public"


SHARED_VISIBILITIES = (Visibility.NETWORK, Visibility.PUBLIC)


class OrgScopedQuerySet(models.QuerySet):
    """QuerySet for any model that declares `ORG_PATH`."""

    def _org_path(self) -> str:
        path = getattr(self.model, "ORG_PATH", None)
        if not path:
            raise TypeError(f"{self.model.__name__} has no ORG_PATH; it is not org-scoped")
        return path

    def for_organization(self, organization) -> OrgScopedQuerySet:
        return self.filter(**{self._org_path(): organization})

    def for_organizations(self, organizations) -> OrgScopedQuerySet:
        return self.filter(**{f"{self._org_path()}__in": organizations})

    def for_user(self, user, min_role: str | None = None) -> OrgScopedQuerySet:
        """Rows owned by any organization `user` belongs to (with at least
        `min_role`, if given). Superusers see everything."""
        if not user or not user.is_authenticated:
            return self.none()
        if user.is_superuser:
            return self.all()
        from apps.organizations.models import Membership

        org_ids = Membership.objects.filter(user=user, is_active=True)
        if min_role:
            org_ids = org_ids.filter(role__in=Membership.roles_at_least(min_role))
        return self.for_organizations(org_ids.values("organization_id"))

    def visible_to_organization(self, organization) -> OrgScopedQuerySet:
        """Rows `organization` owns, plus rows other organizations shared
        (NETWORK or PUBLIC) — only for models with a `visibility` field."""
        q = Q(**{self._org_path(): organization})
        if _has_field(self.model, "visibility"):
            q |= Q(visibility__in=SHARED_VISIBILITIES)
        return self.filter(q)

    def public(self) -> OrgScopedQuerySet:
        if not _has_field(self.model, "visibility"):
            return self.none()
        return self.filter(visibility=Visibility.PUBLIC)


OrgScopedManager = models.Manager.from_queryset(OrgScopedQuerySet)


def _has_field(model, name: str) -> bool:
    try:
        model._meta.get_field(name)
    except Exception:
        return False
    return True


def resolve_organization(obj):
    """Walk `obj`'s ORG_PATH ("event__organization") to its Organization, or
    None if any hop is unset (e.g. an unsaved inline)."""
    path = getattr(type(obj), "ORG_PATH", None)
    if not path:
        return None
    if path == "pk":  # Organization itself
        return obj
    current = obj
    for part in path.split("__"):
        current = getattr(current, part, None)
        if current is None:
            return None
    return current
