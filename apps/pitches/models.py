"""Pitches (session proposals) and the review workflow.

A `Pitch` is written once by a speaker and submitted to one or more events
(possibly run by different organizations) during their pitch windows. Each
(pitch, event) pair is a `PitchTarget` with its own review status, so NICAR
can accept the same pitch that another org's conference waitlists.

Review lifecycle per target (separating *deciding* from *telling*)::

    SUBMITTED ─▶ ACCEPT_QUEUE ─┐               (staff decide in bulk...)
              ─▶ DECLINE_QUEUE ┤
              ─▶ WAITLISTED    │
                               ▼
                   inform() ─▶ ACCEPTED / DECLINED   (...then notify at once)
                               │  informed_at set
                               ▼
                   speaker confirms ─▶ confirmed_at set

`accept()` (pitches.services) turns an accepted target into a
program.Session (source=PITCH) with its speakers attached. Targets can be
merged into another target ("combine these two pitches into one panel");
merged targets' speakers join the surviving session on acceptance.

Each event can add custom questions (`FormQuestion`) to its pitch form and
speaker-info form; answers are stored as JSON keyed by question slug.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import OrgScopedManager, TimeStampedModel


class FormQuestion(models.Model):
    """A custom question on an event's pitch or speaker-info form."""

    class Form(models.TextChoices):
        PITCH = "pitch", "Pitch form"
        SPEAKER_INFO = "speaker_info", "Speaker info form"

    class Kind(models.TextChoices):
        SHORT_TEXT = "short_text", "Short text"
        LONG_TEXT = "long_text", "Long text"
        EMAIL = "email", "Email"
        URL = "url", "URL"
        INTEGER = "integer", "Whole number"
        BOOLEAN = "boolean", "Yes / no"
        SINGLE_CHOICE = "single_choice", "Single choice"
        MULTI_CHOICE = "multi_choice", "Multiple choice"

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="form_questions")
    form = models.CharField(max_length=16, choices=Form.choices, default=Form.PITCH)
    slug = models.SlugField(help_text="Key used in stored answers. Don't change once answers exist.")
    prompt = models.CharField(max_length=500)
    help_text = models.TextField(blank=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.SHORT_TEXT)
    choices = ArrayField(models.CharField(max_length=255), default=list, blank=True)
    required = models.BooleanField(default=False)
    is_private = models.BooleanField(
        default=True, help_text="Never published (all answers are private unless this is unchecked)."
    )
    sort_order = models.PositiveIntegerField(default=0)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "form", "sort_order", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["event", "form", "slug"], name="unique_form_question_slug"),
        ]

    def __str__(self) -> str:
        return self.prompt

    def clean(self):
        if self.kind in (self.Kind.SINGLE_CHOICE, self.Kind.MULTI_CHOICE) and not self.choices:
            raise ValidationError({"choices": "Choice questions need choices."})


class Pitch(TimeStampedModel):
    """A session proposal written by a speaker (global; targets are per event)."""

    submitted_by = models.ForeignKey(
        "speakers.SpeakerProfile", on_delete=models.PROTECT, related_name="pitches_submitted"
    )
    submitted_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    title = models.CharField(max_length=500)
    description = models.TextField()
    format = models.CharField(max_length=100, blank=True, help_text='Suggested format, e.g. "Panel", "Hands-on".')
    skill_level = models.CharField(max_length=100, blank=True)
    proposed_speakers = models.ManyToManyField(
        "speakers.SpeakerProfile", through="PitchSpeaker", related_name="pitches", blank=True
    )
    speaker_suggestions = models.TextField(
        blank=True, help_text="Free text: people the pitcher suggests but who have no profile yet."
    )
    willing_to_organize = models.BooleanField(
        default=True, help_text="Pitcher will plan/moderate the session if accepted."
    )
    submitted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name_plural = "pitches"

    def __str__(self) -> str:
        return self.title


class PitchSpeaker(models.Model):
    """A speaker proposed on a pitch, with their intended role."""

    pitch = models.ForeignKey(Pitch, on_delete=models.CASCADE, related_name="pitch_speakers")
    speaker = models.ForeignKey("speakers.SpeakerProfile", on_delete=models.CASCADE, related_name="+")
    role = models.CharField(max_length=16, default="speaker", help_text="program.SessionSpeaker.Role value.")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["pitch", "sort_order", "pk"]
        constraints = [models.UniqueConstraint(fields=["pitch", "speaker"], name="unique_pitch_speaker")]

    def __str__(self) -> str:
        return f"{self.speaker} on {self.pitch}"


class PitchTarget(TimeStampedModel):
    """One pitch submitted to one event — the unit staff review."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        ACCEPT_QUEUE = "accept_queue", "Accept (not yet informed)"
        DECLINE_QUEUE = "decline_queue", "Decline (not yet informed)"
        WAITLISTED = "waitlisted", "Waitlisted"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        MERGED = "merged", "Merged into another pitch"
        WITHDRAWN = "withdrawn", "Withdrawn by pitcher"

    QUEUED = (Status.ACCEPT_QUEUE, Status.DECLINE_QUEUE)
    FINAL = (Status.ACCEPTED, Status.DECLINED, Status.MERGED, Status.WITHDRAWN)

    pitch = models.ForeignKey(Pitch, on_delete=models.CASCADE, related_name="targets")
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="pitch_targets")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUBMITTED)
    answers = models.JSONField(default=dict, blank=True, help_text="Custom FormQuestion answers by slug.")
    suggested_tracks = models.ManyToManyField("program.Track", blank=True, related_name="pitch_targets")
    staff_notes = models.TextField(blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    informed_at = models.DateTimeField(null=True, blank=True, help_text="When the pitcher was notified.")
    confirmed_at = models.DateTimeField(null=True, blank=True, help_text="When the pitcher confirmed.")
    merged_into = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="merged_targets"
    )
    session = models.OneToOneField(
        "program.Session", null=True, blank=True, on_delete=models.SET_NULL, related_name="pitch_target"
    )

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["pitch", "event"], name="unique_pitch_target")]

    def __str__(self) -> str:
        return f"{self.pitch} → {self.event}"

    def clean(self):
        if self.merged_into_id:
            if self.merged_into_id == self.pk:
                raise ValidationError({"merged_into": "Can't merge a pitch into itself."})
            if self.merged_into.event_id != self.event_id:
                raise ValidationError({"merged_into": "Can only merge pitches for the same event."})


class PitchVote(TimeStampedModel):
    """One staffer's vote on a pitch target (replaces the per-staffer columns)."""

    class Vote(models.TextChoices):
        YES = "yes", "Yes"
        MAYBE = "maybe", "Maybe"
        NO = "no", "No"

    target = models.ForeignKey(PitchTarget, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pitch_votes")
    vote = models.CharField(max_length=8, choices=Vote.choices)
    comment = models.TextField(blank=True)

    ORG_PATH = "target__event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["target", "user"]
        constraints = [models.UniqueConstraint(fields=["target", "user"], name="unique_pitch_vote")]

    def __str__(self) -> str:
        return f"{self.user}: {self.vote} on {self.target}"
