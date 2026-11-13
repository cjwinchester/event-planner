"""Tenants (organizations) and who belongs to them.

An `Organization` is one participating nonprofit (e.g. IRE). Staff belong to
organizations through `Membership`, which carries a role:

    ADMIN  — "Organization Administrator": everything in the org, including
             integrations, sharing settings and memberships.
    STAFF  — planners: events, sessions, speakers, pitches, sponsors.
    VIEWER — read-only access to the org's data.

The installation-wide "Super Admin" is simply `User.is_superuser`.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from apps.core.models import OrgScopedManager, TimeStampedModel, Visibility
from apps.core.validators import validate_timezone


class Organization(TimeStampedModel):
    """A participating nonprofit — the tenant boundary."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, help_text="Used in public URLs and published file paths.")
    website = models.URLField(blank=True)
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to="organizations/logos/", blank=True)
    default_timezone = models.CharField(max_length=64, default="America/Chicago", validators=[validate_timezone])
    is_active = models.BooleanField(default=True)

    # --- Sharing defaults (Org Admin controls what is shared) -------------
    default_event_visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.NETWORK,
        help_text="Default for new events. NETWORK puts them on the shared cross-org calendar.",
    )
    default_note_visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
        help_text="Default for new venue notes/contacts this org writes.",
    )

    # --- SSO --------------------------------------------------------------
    sso_domains = ArrayField(
        models.CharField(max_length=255),
        default=list,
        blank=True,
        help_text="Google Workspace domains (e.g. 'ire.org') whose users may join via SSO.",
    )
    sso_default_role = models.CharField(
        max_length=16,
        blank=True,
        help_text="If set, a first-time Google login from an sso_domain is auto-added with this role. "
        "Leave blank to require an admin to add members manually.",
    )

    # --- Public IDs -------------------------------------------------------
    session_id_start = models.PositiveIntegerField(
        default=1000,
        help_text="First public session number for a new event (public IDs are stable across "
        "republishes; existing schedule UIs key favorites on them).",
    )

    ORG_PATH = "pk"  # an Organization "belongs" to itself for scoping purposes

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Membership(TimeStampedModel):
    """A user's role in one organization."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Organization administrator"
        STAFF = "staff", "Staff planner"
        VIEWER = "viewer", "Viewer (read-only)"

    # Ordered weakest → strongest; used by roles_at_least().
    ROLE_ORDER = [Role.VIEWER, Role.STAFF, Role.ADMIN]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.STAFF)
    is_active = models.BooleanField(default=True)
    title = models.CharField(max_length=255, blank=True, help_text="Job title, shown to colleagues.")

    ORG_PATH = "organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["organization", "user"]
        constraints = [
            models.UniqueConstraint(fields=["user", "organization"], name="unique_membership"),
        ]

    def __str__(self) -> str:
        return f"{self.user} — {self.organization} ({self.get_role_display()})"

    @classmethod
    def roles_at_least(cls, role: str) -> list[str]:
        idx = cls.ROLE_ORDER.index(role)
        return [str(r) for r in cls.ROLE_ORDER[idx:]]
