"""Per-organization integration settings, sync history, and external IDs.

- `IntegrationConfig` turns a connector on for an organization and stores
  its credentials, encrypted at rest. Credential *scope* (borrowed from the
  earlier conference-planning-dashboard attempt): each connector declares
  which credential fields are org-wide (an AMS login) and which are
  per-event (a webinar series ID). Org-scoped rows have `event=NULL`;
  event-scoped rows set `event`. `get_credentials()` merges both levels.
- `SyncRun` records every connector run (status, counts, error).
- `ExternalIdentity` maps any local row to its ID in an outside system
  (a speaker ↔ MemberSuite Individual, an event ↔ MemberSuite Event,
  a session ↔ Zoom webinar), so connectors can upsert idempotently.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.core.fields import EncryptedJSONField
from apps.core.models import OrgScopedManager, TimeStampedModel


class IntegrationConfig(TimeStampedModel):
    """One connector enabled for an organization (or one of its events)."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="integration_configs"
    )
    event = models.ForeignKey(
        "events.Event",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="integration_configs",
        help_text="Blank for org-wide settings; set for per-event credentials.",
    )
    connector = models.CharField(max_length=64, help_text="Connector slug from the registry.")
    is_enabled = models.BooleanField(default=True)
    credentials = EncryptedJSONField(help_text="Secret values, encrypted at rest.")
    options = models.JSONField(default=dict, blank=True, help_text="Non-secret settings (bucket, prefix...).")
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_test_ok = models.BooleanField(null=True, blank=True)
    last_test_message = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    ORG_PATH = "organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["organization", "connector"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "connector"],
                condition=models.Q(event__isnull=True),
                name="unique_org_scoped_integration",
            ),
            models.UniqueConstraint(
                fields=["organization", "event", "connector"],
                condition=models.Q(event__isnull=False),
                name="unique_event_scoped_integration",
            ),
        ]

    def __str__(self) -> str:
        scope = f" / {self.event}" if self.event_id else ""
        return f"{self.organization}{scope}: {self.connector}"

    @classmethod
    def get_credentials(cls, organization, connector: str, event=None) -> tuple[dict, dict] | None:
        """(credentials, options) merged org-level then event-level, or None
        if the connector isn't enabled for the organization."""
        org_row = cls.objects.filter(
            organization=organization, connector=connector, event__isnull=True, is_enabled=True
        ).first()
        event_row = (
            cls.objects.filter(organization=organization, connector=connector, event=event, is_enabled=True).first()
            if event is not None
            else None
        )
        if org_row is None and event_row is None:
            return None
        credentials, options = {}, {}
        for row in (org_row, event_row):
            if row is not None:
                credentials.update(row.credentials or {})
                options.update(row.options or {})
        return credentials, options


class SyncRun(models.Model):
    """One execution of a connector operation."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    event = models.ForeignKey("events.Event", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    connector = models.CharField(max_length=64)
    operation = models.CharField(max_length=64, help_text='e.g. "sync_registrations".')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    stats = models.JSONField(default=dict, blank=True, help_text="created/updated/skipped/failed counts.")
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    ORG_PATH = "organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.connector}.{self.operation} ({self.status})"


class ExternalIdentity(models.Model):
    """Links a local object to its ID in an external system, per organization."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    connector = models.CharField(max_length=64)
    external_id = models.CharField(max_length=255)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")
    external_url = models.URLField(blank=True, help_text="Link to the record in the other system.")
    data = models.JSONField(default=dict, blank=True, help_text="Last payload seen, for reference.")
    last_synced_at = models.DateTimeField(null=True, blank=True)

    ORG_PATH = "organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["organization", "connector"]
        verbose_name_plural = "external identities"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "connector", "content_type", "external_id"],
                name="unique_external_identity",
            ),
        ]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self) -> str:
        return f"{self.connector}:{self.external_id} → {self.content_type.model} {self.object_id}"
