"""Speakers: one global profile per person, private per-org engagement data,
and speaker-controlled sharing.

Global profile vs. org data
---------------------------
`SpeakerProfile` is shared across the installation (the "shared pool of
speakers"). Its fields split in two:

  PUBLIC_FIELDS  — name, bio, headshot, affiliation, links: visible to every
                   organization (and published in schedules).
  PRIVATE_FIELDS — email, phone, location: visible only to organizations
                   the speaker has an active `SpeakerShareGrant` with.

Everything an organization records about working with a speaker (status,
comps, demographics/form answers, internal notes) lives on that org's
`EventSpeaker` rows and is never visible to other organizations.

Sharing consent (`SpeakerShareGrant`)
-------------------------------------
The speaker decides which organizations see their private fields. By
default, pitching to an organization grants it (pitches.services). Grants
created automatically never override a speaker's explicit revocation,
except a new pitch — an explicit act by the speaker — which re-grants.
See `speakers.sharing`.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.core.models import OrgScopedManager, TimeStampedModel


def _new_public_id() -> str:
    return secrets.token_hex(4)


class SpeakerProfile(TimeStampedModel):
    """A person who speaks (or might). Global; may be claimed by a User."""

    PUBLIC_FIELDS = (
        "public_id", "first_name", "last_name", "pronouns", "affiliation", "job_title",
        "bio", "headshot", "headshot_url", "links",
    )  # fmt: skip
    PRIVATE_FIELDS = ("email", "phone", "city", "region", "country")

    public_id = models.CharField(
        max_length=16, unique=True, default=_new_public_id, editable=False, help_text="Stable public ID."
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="speaker_profile",
        help_text="Set when the speaker claims this profile by verifying the email.",
    )
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255, blank=True)
    pronouns = models.CharField(max_length=64, blank=True)
    affiliation = models.CharField(max_length=255, blank=True, help_text="Employer/organization, as published.")
    job_title = models.CharField(max_length=255, blank=True)
    bio = models.TextField(blank=True, help_text="Third person. HTML allowed (sanitized on export).")
    headshot = models.ImageField(upload_to="speakers/headshots/", blank=True)
    headshot_url = models.URLField(blank=True, help_text="External headshot, used when no file is uploaded.")
    # Private fields (see SpeakerShareGrant)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=64, blank=True)
    city = models.CharField(max_length=255, blank=True)
    region = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=2, blank=True)

    is_active = models.BooleanField(default=True)
    merged_into = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="merged_profiles",
        help_text="Set when this duplicate was merged into another profile (see speakers.services.merge).",
    )
    created_by_organization = models.ForeignKey(
        "organizations.Organization", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                condition=~models.Q(email="") & models.Q(merged_into__isnull=True),
                name="unique_active_speaker_email_ci",
            ),
        ]
        indexes = [models.Index(Lower("last_name"), Lower("first_name"), name="speaker_name_ci_idx")]

    def __str__(self) -> str:
        return self.full_name

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def headshot_href(self) -> str:
        return self.headshot.url if self.headshot else self.headshot_url


class SpeakerLink(models.Model):
    """A typed social/profile link (maps to the published `social` array)."""

    class Kind(models.TextChoices):
        WEBSITE = "website", "Website"
        BLOG = "blog", "Blog"
        LINKEDIN = "linkedin", "LinkedIn"
        BLUESKY = "bluesky", "Bluesky"
        MASTODON = "mastodon", "Mastodon"
        THREADS = "threads", "Threads"
        X = "x", "X / Twitter"
        INSTAGRAM = "instagram", "Instagram"
        FACEBOOK = "facebook", "Facebook"
        TIKTOK = "tiktok", "TikTok"
        YOUTUBE = "youtube", "YouTube"
        GITHUB = "github", "GitHub"
        OTHER = "other", "Other"

    speaker = models.ForeignKey(SpeakerProfile, on_delete=models.CASCADE, related_name="links")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.WEBSITE)
    url = models.URLField(max_length=500)
    label = models.CharField(max_length=100, blank=True, help_text="Defaults to the kind's name.")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["speaker", "sort_order", "pk"]

    def __str__(self) -> str:
        return f"{self.display_label}: {self.url}"

    @property
    def display_label(self) -> str:
        return self.label or self.get_kind_display()


class SpeakerShareGrant(TimeStampedModel):
    """Speaker-controlled consent for one organization to see private fields."""

    class Source(models.TextChoices):
        PITCH = "pitch", "Speaker pitched to this organization"
        SPEAKER = "speaker", "Speaker granted it directly"
        ENGAGEMENT = "engagement", "Organization added the speaker to an event"
        CREATED = "created", "Organization created the profile"

    speaker = models.ForeignKey(SpeakerProfile, on_delete=models.CASCADE, related_name="share_grants")
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="speaker_grants"
    )
    source = models.CharField(max_length=16, choices=Source.choices)
    granted_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by_speaker = models.BooleanField(default=False)

    ORG_PATH = "organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["speaker", "organization"]
        constraints = [
            models.UniqueConstraint(fields=["speaker", "organization"], name="unique_speaker_share_grant"),
        ]

    def __str__(self) -> str:
        state = "revoked" if self.revoked_at else "active"
        return f"{self.speaker} → {self.organization} ({state})"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class EventSpeaker(TimeStampedModel):
    """One speaker's engagement with one event — org-private working data.

    Mirrors the old SuperGrid "speakers" tab: confirmation, who contacted
    them, comp/discount requests, registration and membership status,
    per-event bio/affiliation edits and the speaker-info form answers.
    """

    class Status(models.TextChoices):
        PROSPECT = "prospect", "Prospect (not yet asked)"
        INVITED = "invited", "Invited / asked"
        CONFIRMED = "confirmed", "Confirmed"
        DECLINED = "declined", "Declined"
        WITHDRAWN = "withdrawn", "Withdrew"

    class Comp(models.TextChoices):
        NONE = "none", "No request"
        REQUESTED = "requested", "Comp requested"
        GRANTED = "granted", "Comp granted"
        REDUCED = "reduced", "Reduced rate"
        DENIED = "denied", "Denied"

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="event_speakers")
    speaker = models.ForeignKey(SpeakerProfile, on_delete=models.CASCADE, related_name="engagements")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.INVITED)
    contacted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    comp_status = models.CharField(max_length=16, choices=Comp.choices, default=Comp.NONE)
    registration_status = models.CharField(max_length=255, blank=True, help_text="e.g. 'Registered', 'Comp added'.")
    membership_status = models.CharField(max_length=255, blank=True, help_text="From the AMS connector.")
    is_featured = models.BooleanField(default=False, help_text="Highlight (keynote/featured speaker).")
    affiliation_override = models.CharField(max_length=255, blank=True)
    bio_override = models.TextField(blank=True, help_text="Edited bio for this event (wins over profile bio).")
    bio_edited = models.BooleanField(default=False)
    recording_consent = models.BooleanField(null=True, blank=True)
    form_responses = models.JSONField(
        default=dict,
        blank=True,
        help_text="Speaker-info form answers (agreements, accessibility needs, demographics). Org-private.",
    )
    notes = models.TextField(blank=True)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "speaker"]
        constraints = [models.UniqueConstraint(fields=["event", "speaker"], name="unique_event_speaker")]

    def __str__(self) -> str:
        return f"{self.speaker} @ {self.event}"

    @property
    def display_bio(self) -> str:
        return self.bio_override or self.speaker.bio

    @property
    def display_affiliation(self) -> str:
        return self.affiliation_override or self.speaker.affiliation
