"""Hotel links: a read-only page for a hotel's event staff, plus the list of
schedule changes they need to retype into their board readers.

Hotels program digital signs ("board readers") by hand from the schedule we
send them. Close to the event, every change has to be communicated and
re-entered, and there's no integration with the boards. So:

  * `HotelLink` is a private, revocable, expiring URL (no login), optionally
    limited to one venue's rooms, showing every programmed time block in every
    room plus spec details (setup, A/V, F&B, notes);
  * from the event's cutoff (`Event.hotel_changes_from`, default the day before
    it starts) every board-visible change to a session is recorded as a
    `ScheduleChange`: one open row per session holding what the boards had
    ("before") and what they should show now ("after"); repeated edits update
    that row and a change reverted to the original disappears;
  * an Organization admin marks each change done once they've confirmed the
    hotel updated its boards. A later change to the same session opens a new row.

Board-visible = title, room, start and end of a session placed in a room at a
time. Unplacing, canceling or deleting a session removes it from the boards.
"""

from __future__ import annotations

import secrets
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import OrgScopedManager


def new_token() -> str:
    return secrets.token_urlsafe(24)


def default_cutoff(event) -> datetime:
    """The day before the event starts, at midnight in the event's timezone."""
    return datetime.combine(event.start_date - timedelta(days=1), time.min, tzinfo=event.tz)


def changes_from(event) -> datetime:
    return event.hotel_changes_from or default_cutoff(event)


class HotelLink(models.Model):
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="hotel_links")
    venue = models.ForeignKey(
        "venues.Venue",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="hotel_links",
        help_text="Only this venue's rooms (blank: every room at the event).",
    )
    label = models.CharField(max_length=255, help_text="Who it's for, e.g. JW Marriott banquets.")
    token = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(help_text="Defaults to 7 days after the event ends.")
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_viewed_at = models.DateTimeField(null=True, blank=True)
    view_count = models.PositiveIntegerField(default=0)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "label"]

    def __str__(self) -> str:
        return f"{self.label} ({self.event})"

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = datetime.combine(self.event.end_date + timedelta(days=8), time.min, tzinfo=self.event.tz)
        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and timezone.now() < self.expires_at


class ScheduleChange(models.Model):
    class Kind(models.TextChoices):
        ADDED = "added", "New session"
        CHANGED = "changed", "Changed"
        REMOVED = "removed", "Removed from the schedule"

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="schedule_changes")
    # A plain id, not a FK: the row must outlive a deleted session.
    session_key = models.PositiveBigIntegerField(db_index=True)
    before = models.JSONField(null=True, blank=True, help_text="What the boards had (null: wasn't on them).")
    after = models.JSONField(null=True, blank=True, help_text="What the boards should show (null: take it off).")
    first_recorded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    done_at = models.DateTimeField(null=True, blank=True)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["event", "done_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "session_key"], condition=models.Q(done_at__isnull=True), name="one_open_change"
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.title}"

    @property
    def kind(self) -> str:
        if self.before is None:
            return self.Kind.ADDED
        if self.after is None:
            return self.Kind.REMOVED
        return self.Kind.CHANGED

    def get_kind_display(self) -> str:
        return self.Kind(self.kind).label

    @property
    def title(self) -> str:
        return (self.after or self.before or {}).get("title", "")

    def changed_fields(self) -> list[str]:
        if self.before is None or self.after is None:
            return []
        return [f for f in ("title", "room", "starts_at", "ends_at") if self.before.get(f) != self.after.get(f)]

    @property
    def title_changed(self) -> bool:
        return "title" in self.changed_fields()
