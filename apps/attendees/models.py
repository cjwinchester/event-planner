"""Personal, private schedule links ("passes") for attendees and speakers.

Join links for virtual sessions must never appear in anything published.
Instead every registrant (and speaker) gets a pass: an unguessable URL to
their own schedule page and calendar feed, whose Join buttons go through
`/my/<token>/join/<session>/`, which looks up the session's *current* link
(`program.models.join_url_for`) and redirects. So:

  - links stay current when a meeting is recreated or a session moves;
  - a canceled registration or a revoked pass stops working at once, and a
    forwarded pass can be reissued without touching anyone else's;
  - each click is logged (`JoinClick`), which gives per-person attendance
    without storing anything from Zoom's participant reports.

Passes contain registrant names and emails, so they are org-private like
registrations: never in a bundle, feed or another org's view.
"""

from __future__ import annotations

import secrets

from django.db import models
from django.db.models import F, Q, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.core.models import OrgScopedManager, TimeStampedModel


def new_token() -> str:
    return secrets.token_urlsafe(24)


class AttendeePass(TimeStampedModel):
    class Kind(models.TextChoices):
        ATTENDEE = "attendee", "Attendee"
        SPEAKER = "speaker", "Speaker"
        GUEST = "guest", "Guest"

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="passes")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.ATTENDEE)
    registration = models.ForeignKey(
        "events.Registration", null=True, blank=True, on_delete=models.SET_NULL, related_name="passes"
    )
    speaker = models.ForeignKey(
        "speakers.SpeakerProfile", null=True, blank=True, on_delete=models.SET_NULL, related_name="passes"
    )
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    token = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    revoked_at = models.DateTimeField(null=True, blank=True)
    first_used_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    use_count = models.PositiveIntegerField(default=0, help_text="Page views, calendar fetches and joins.")

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "registration"],
                condition=Q(registration__isnull=False),
                name="one_pass_per_registration",
            ),
            models.UniqueConstraint(
                fields=["event", "speaker"], condition=Q(speaker__isnull=False), name="one_pass_per_speaker"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_kind_display()})"

    @property
    def is_active(self) -> bool:
        if self.revoked_at:
            return False
        reg = self.registration
        return reg is None or reg.canceled_at is None

    def reissue(self) -> None:
        """New token (the old link stops working), e.g. after it was forwarded."""
        self.token = new_token()
        self.revoked_at = None
        self.save(update_fields=["token", "revoked_at", "updated_at"])

    def touch(self) -> None:
        now = timezone.now()
        AttendeePass.objects.filter(pk=self.pk).update(
            use_count=F("use_count") + 1,
            last_used_at=now,
            first_used_at=Coalesce("first_used_at", Value(now)),
        )


class JoinClick(models.Model):
    """One press of a Join button. `device` is a salted hash of IP + user
    agent: enough to notice one pass used from many places, not to track."""

    attendee_pass = models.ForeignKey(AttendeePass, on_delete=models.CASCADE, related_name="clicks")
    session = models.ForeignKey("program.Session", on_delete=models.CASCADE, related_name="join_clicks")
    at = models.DateTimeField(default=timezone.now)
    device = models.CharField(max_length=16, blank=True)

    ORG_PATH = "attendee_pass__event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["-at"]
        indexes = [models.Index(fields=["session", "at"])]

    def __str__(self) -> str:
        return f"{self.attendee_pass} → {self.session} at {self.at:%Y-%m-%d %H:%M}"
