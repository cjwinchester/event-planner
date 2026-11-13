"""Publish history.

Every publish builds one Event Bundle (the canonical JSON — see
schemas/event-bundle/v1.json and publishing.bundle), renders each requested
format from it, and uploads the files to the organization's storage target
under stable paths. `PublishRun` records what was written where, so the
admin can show "last published" and link every feed.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import OrgScopedManager


class PublishRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        PARTIAL = "partial", "Partly succeeded (some formats failed)"
        FAILED = "failed", "Failed"

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="publish_runs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    target = models.CharField(max_length=64, help_text='Storage target used ("local", "s3").')
    formats = models.JSONField(default=list, help_text="Exporter slugs requested.")
    files = models.JSONField(default=list, blank=True, help_text='[{"format", "path", "url", "bytes", "sha256"}, ...]')
    bundle_version = models.CharField(max_length=16, blank=True)
    session_count = models.PositiveIntegerField(default=0)
    dry_run = models.BooleanField(default=False)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.event} publish {self.pk} ({self.status})"
