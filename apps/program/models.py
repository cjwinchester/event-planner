"""The program: what happens when and where.

Two kinds of things occupy rooms and time:

- `Session` — content (panels, hands-on classes, keynotes, networking).
- `LogisticsItem` — non-content room usage (registration desk, coat check,
  nursing room, storage, staff office, catering/water service).

Both share `RoomUsage` fields (room, time, setup, headcount, A/V, F&B) so a
hotel spec sheet ("For Hotel" BEO orders) can be generated from either.
Conflict detection over both lives in `program.conflicts`; *all* schedule
changes from the grid/API go through `program.scheduling.place_item()`,
which locks the event row so two planners can't double-book a room at once.

Times: every start/end is an aware datetime stored in UTC; render in
`event.timezone`. `TimeSlot`s are the grid's rows (per day, any length) —
guides for placing sessions, not a constraint on them.

"Ready" (publishable) is computed, not a flag: see `Session.is_ready`.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Max, Q

from apps.core.models import OrgScopedManager, OrgScopedQuerySet, TimeStampedModel
from apps.venues.models import RoomCapacity


class Track(models.Model):
    """A thematic stream within one event ("Public records", "AI")."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="tracks")
    name = models.CharField(max_length=255)
    slug = models.SlugField()
    description = models.TextField(blank=True)
    color = models.CharField(max_length=7, blank=True, help_text="Hex color for grid/UI, e.g. #1f77b4.")
    sort_order = models.PositiveIntegerField(default=0)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["event", "slug"], name="unique_track_slug_per_event")]

    def __str__(self) -> str:
        return self.name


class SessionType(models.Model):
    """Org-level lookup: Panel, Hands-on, Demo, Keynote, Networking..."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="session_types"
    )
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, blank=True)
    default_duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    is_hands_on = models.BooleanField(default=False, help_text="Computer-lab style; enables lab fields.")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    ORG_PATH = "organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["organization", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="unique_session_type_per_org"),
        ]

    def __str__(self) -> str:
        return self.name


class SkillLevel(models.Model):
    """Org-level lookup: Beginner, Intermediate, Advanced..."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="skill_levels"
    )
    name = models.CharField(max_length=100)
    sort_order = models.PositiveIntegerField(default=0)

    ORG_PATH = "organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["organization", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="unique_skill_level_per_org"),
        ]

    def __str__(self) -> str:
        return self.name


class TimeSlot(models.Model):
    """A row in the planning grid for one day, e.g. Thu 10:15–11:15.

    Slots are per day (conference days rarely share one rhythm: pre-blocks,
    first-timer sessions, short Sunday). Sessions may still start/end off
    the slot boundaries — slots are guides, not constraints.
    """

    class Kind(models.TextChoices):
        CONTENT = "content", "Content block"
        BREAK = "break", "Break"
        MEAL = "meal", "Meal"
        PLENARY = "plenary", "Plenary / all-attendee"

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="time_slots")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    label = models.CharField(max_length=255, blank=True, help_text='e.g. "Pre-block 2 (First-timers)".')
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.CONTENT)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "starts_at"]
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=models.F("starts_at")), name="timeslot_ends_after_start"),
        ]

    def __str__(self) -> str:
        tz = self.event.tz
        start, end = self.starts_at.astimezone(tz), self.ends_at.astimezone(tz)
        text = f"{start:%a %-I:%M}–{end:%-I:%M %p}"
        return f"{text} {self.label}".strip()

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "End must be after start."})
        if self.event_id and self.starts_at and self.ends_at:
            overlapping = (
                TimeSlot.objects.filter(event_id=self.event_id, starts_at__lt=self.ends_at, ends_at__gt=self.starts_at)
                .exclude(pk=self.pk)
                .exists()
            )
            if overlapping:
                raise ValidationError("This overlaps another time slot for the event.")


class RoomUsage(models.Model):
    """Fields shared by everything that occupies a room at a time.

    Concrete subclasses declare their own `room` FK (to events.EventRoom) so
    each gets a readable reverse accessor (event_room.sessions /
    event_room.logistics_items).
    """

    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    setup = models.CharField(max_length=20, choices=RoomCapacity.Setup.choices, blank=True)
    expected_headcount = models.PositiveIntegerField(null=True, blank=True)
    av_requirements = models.TextField("A/V requirements", blank=True)
    fnb_requirements = models.TextField("F&B requirements", blank=True)
    hotel_notes = models.TextField(blank=True, help_text="Instructions for the venue's BEO / spec sheet.")

    class Meta:
        abstract = True

    @property
    def is_placed(self) -> bool:
        return bool(self.room_id and self.starts_at and self.ends_at)

    @property
    def duration_minutes(self) -> int | None:
        if not (self.starts_at and self.ends_at):
            return None
        return int((self.ends_at - self.starts_at).total_seconds() // 60)

    def _clean_room_usage(self, event):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "End must be after start."})
        if (self.starts_at is None) != (self.ends_at is None):
            raise ValidationError("Set both start and end time, or neither.")
        if self.room_id and event is not None and self.room.event_id != event.pk:
            raise ValidationError({"room": "That room is not set up for this event."})
        if self.starts_at and event is not None:
            if self.starts_at < event.starts_at or self.ends_at > event.ends_at:
                raise ValidationError("Times must fall within the event's dates (in the event's timezone).")


class SessionQuerySet(OrgScopedQuerySet):
    def active(self):
        return self.exclude(status=Session.Status.CANCELED)

    def scheduled(self):
        return self.active().filter(room__isnull=False, starts_at__isnull=False, ends_at__isnull=False)

    def ready(self):
        """Publishable sessions: placed in a room, timed, with an edited title."""
        return self.scheduled().filter(title_status=Session.EditStatus.EDITED)

    def stock(self):
        return self.filter(is_stock=True)


class Session(TimeStampedModel, RoomUsage):
    """A content block at an event."""

    class EditStatus(models.TextChoices):
        PENDING = "pending", "Waiting on speaker/pitcher"
        NEEDS_EDIT = "needs_edit", "Needs edit"
        EDITED = "edited", "Edited"

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        PLANNING = "planning", "Planning"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELED = "canceled", "Canceled"

    class Source(models.TextChoices):
        STAFF = "staff", "Staff-planned"
        PITCH = "pitch", "From a pitch"
        STOCK = "stock", "Copied from a stock/previous session"
        SPONSOR = "sponsor", "Sponsor-provided"
        IMPORT = "import", "Imported"

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="sessions")
    room = models.ForeignKey(
        "events.EventRoom", null=True, blank=True, on_delete=models.SET_NULL, related_name="sessions"
    )
    public_id = models.PositiveIntegerField(
        editable=False,
        help_text="Stable public number (used in feed IDs, ICS UIDs, attendee favorites). Never reused.",
    )
    title = models.CharField(max_length=500)
    title_status = models.CharField(max_length=16, choices=EditStatus.choices, default=EditStatus.PENDING)
    description = models.TextField(blank=True, help_text="HTML allowed (sanitized on export).")
    description_status = models.CharField(max_length=16, choices=EditStatus.choices, default=EditStatus.PENDING)
    summary = models.CharField(max_length=500, blank=True, help_text="One-line teaser.")
    session_type = models.ForeignKey(
        SessionType, null=True, blank=True, on_delete=models.SET_NULL, related_name="sessions"
    )
    skill_level = models.ForeignKey(
        SkillLevel, null=True, blank=True, on_delete=models.SET_NULL, related_name="sessions"
    )
    tracks = models.ManyToManyField(Track, blank=True, related_name="sessions")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNING)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.STAFF)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_sessions",
        help_text="Staffer responsible for this session.",
    )
    is_plenary = models.BooleanField(
        default=False,
        help_text="All-attendee session (keynote, welcome). Overlapping sessions are flagged as warnings.",
    )
    is_stock = models.BooleanField(
        default=False, help_text="Recurring/stock session — offered by the event copy utility."
    )
    copied_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="copies")
    requires_preregistration = models.BooleanField(default=False)
    capacity = models.PositiveIntegerField(null=True, blank=True, help_text="Seat limit, if any.")
    cost = models.CharField(max_length=64, blank=True, help_text='Add-on price text, e.g. "$25".')
    is_recorded = models.BooleanField(null=True, blank=True, help_text="Blank = room default.")
    live_url = models.URLField(blank=True, help_text="Stream/meeting link (Zoom, etc.).")
    recording_url = models.URLField(blank=True, help_text="Recording link (Vimeo, etc.).")
    working_folder_url = models.URLField(blank=True, help_text="Internal planning folder (Drive...).")
    internal_notes = models.TextField(blank=True)
    extra = models.JSONField(
        default=dict,
        blank=True,
        help_text="Event-specific fields and custom question answers "
        "(e.g. software, lab OS, data_submitted, repo_url), keyed by question slug.",
    )

    ORG_PATH = "event__organization"
    objects = models.Manager.from_queryset(SessionQuerySet)()

    class Meta:
        ordering = ["event", "starts_at", "title"]
        constraints = [
            models.UniqueConstraint(fields=["event", "public_id"], name="unique_session_public_id"),
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(starts_at__isnull=True) | Q(ends_at__gt=models.F("starts_at")),
                name="session_ends_after_start",
            ),
        ]
        indexes = [models.Index(fields=["event", "starts_at"]), models.Index(fields=["event", "room"])]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if self.public_id is None:
            self.public_id = self._next_public_id()
        super().save(*args, **kwargs)

    def _next_public_id(self) -> int:
        """Next number for this event. Concurrent creates can collide on the
        unique constraint; program.scheduling serializes writes per event."""
        current = Session.objects.filter(event_id=self.event_id).aggregate(m=Max("public_id"))["m"]
        if current is not None:
            return current + 1
        return self.event.organization.session_id_start

    def clean(self):
        self._clean_room_usage(self.event if self.event_id else None)
        if self.session_type_id and self.event_id:
            if self.session_type.organization_id != self.event.organization_id:
                raise ValidationError({"session_type": "Session type belongs to another organization."})
        if self.skill_level_id and self.event_id:
            if self.skill_level.organization_id != self.event.organization_id:
                raise ValidationError({"skill_level": "Skill level belongs to another organization."})
        # Conflict checks (room + speaker) — blocking errors only; warnings
        # are surfaced by the grid/API instead.
        if self.event_id and self.is_placed and self.status != self.Status.CANCELED:
            from .conflicts import Severity, check_session

            errors = [c for c in check_session(self) if c.severity == Severity.ERROR]
            if errors:
                raise ValidationError([c.message for c in errors])

    @property
    def is_canceled(self) -> bool:
        return self.status == self.Status.CANCELED

    @property
    def is_ready(self) -> bool:
        """Publishable: placed in a room, timed, title edited, not canceled."""
        return self.is_placed and self.title_status == self.EditStatus.EDITED and not self.is_canceled

    @property
    def readiness_problems(self) -> list[str]:
        problems = []
        if self.is_canceled:
            problems.append("canceled")
        if not self.room_id:
            problems.append("no room")
        if not (self.starts_at and self.ends_at):
            problems.append("no time")
        if self.title_status != self.EditStatus.EDITED:
            problems.append("title not edited")
        return problems

    @property
    def effective_is_recorded(self) -> bool:
        if self.is_recorded is not None:
            return self.is_recorded
        return bool(self.room_id and self.room.is_recorded)


class SessionSpeaker(models.Model):
    """A speaker's role on one session (speaker, moderator, hands-on coach)."""

    class Role(models.TextChoices):
        SPEAKER = "speaker", "Speaker"
        MODERATOR = "moderator", "Moderator"
        COACH = "coach", "Coach / TA"
        HOST = "host", "Host"

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="session_speakers")
    speaker = models.ForeignKey("speakers.SpeakerProfile", on_delete=models.CASCADE, related_name="session_roles")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.SPEAKER)
    sort_order = models.PositiveIntegerField(default=0)
    affiliation_override = models.CharField(max_length=255, blank=True, help_text="As printed for this session.")
    is_confirmed = models.BooleanField(default=False)

    ORG_PATH = "session__event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["session", "sort_order", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["session", "speaker", "role"], name="unique_session_speaker_role"),
        ]

    def __str__(self) -> str:
        return f"{self.speaker} ({self.get_role_display()}) — {self.session}"

    def clean(self):
        if not (self.session_id and self.speaker_id):
            return
        session = self.session
        if session.is_placed and not session.is_canceled:
            from .conflicts import speaker_conflicts

            clashes = speaker_conflicts(
                event_id=session.event_id,
                speaker_ids=[self.speaker_id],
                starts_at=session.starts_at,
                ends_at=session.ends_at,
                exclude_session_id=session.pk,
            )
            if clashes:
                raise ValidationError([c.message for c in clashes])


class SessionAsset(TimeStampedModel):
    """Slides, tipsheets, data, recordings or links attached to a session."""

    class Kind(models.TextChoices):
        SLIDES = "slides", "Slides"
        TIPSHEET = "tipsheet", "Tipsheet / handout"
        DATA = "data", "Data"
        CODE = "code", "Code / repository"
        AUDIO = "audio", "Audio recording"
        VIDEO = "video", "Video recording"
        LINK = "link", "Link"
        OTHER = "other", "Other"

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="assets")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.OTHER)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    file = models.FileField(upload_to="sessions/assets/", blank=True)
    url = models.URLField(blank=True)
    speaker = models.ForeignKey(
        "speakers.SpeakerProfile", null=True, blank=True, on_delete=models.SET_NULL, related_name="assets"
    )
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    is_public = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    ORG_PATH = "session__event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["session", "sort_order", "pk"]

    def __str__(self) -> str:
        return self.title

    def clean(self):
        if bool(self.file) == bool(self.url):
            raise ValidationError("Attach a file or a URL (exactly one).")

    @property
    def href(self) -> str:
        return self.file.url if self.file else self.url


class LogisticsCategory(models.Model):
    """Org-level kinds of logistics: Registration, Coat check, Nursing room,
    Storage, Staff office, Water service, Catering..."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="logistics_categories"
    )
    name = models.CharField(max_length=100)
    blocks_room = models.BooleanField(
        default=True,
        help_text="Uncheck for services that share a room with sessions (water service, catering).",
    )
    public_by_default = models.BooleanField(default=False, help_text="Show on the attendee schedule.")
    color = models.CharField(max_length=7, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    ORG_PATH = "organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["organization", "sort_order", "name"]
        verbose_name_plural = "logistics categories"
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="unique_logistics_category_per_org"),
        ]

    def __str__(self) -> str:
        return self.name


class LogisticsItem(TimeStampedModel, RoomUsage):
    """Non-content room usage tied to a time and place."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="logistics_items")
    room = models.ForeignKey(
        "events.EventRoom", null=True, blank=True, on_delete=models.SET_NULL, related_name="logistics_items"
    )
    category = models.ForeignKey(LogisticsCategory, on_delete=models.PROTECT, related_name="items")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, help_text="Attendee-facing text if public.")
    location_note = models.CharField(
        max_length=255, blank=True, help_text='Free-text place when not a bookable room ("Level 2 foyer").'
    )
    blocks_room = models.BooleanField(null=True, blank=True, help_text="Blank = category default.")
    is_public = models.BooleanField(null=True, blank=True, help_text="Blank = category default. (DNP = no.)")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "starts_at", "title"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(starts_at__isnull=True) | Q(ends_at__gt=models.F("starts_at")),
                name="logistics_ends_after_start",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def effective_blocks_room(self) -> bool:
        return self.category.blocks_room if self.blocks_room is None else self.blocks_room

    @property
    def effective_is_public(self) -> bool:
        return self.category.public_by_default if self.is_public is None else self.is_public

    def clean(self):
        self._clean_room_usage(self.event if self.event_id else None)
        if self.category_id and self.event_id and self.category.organization_id != self.event.organization_id:
            raise ValidationError({"category": "Category belongs to another organization."})
        if self.event_id and self.is_placed and self.effective_blocks_room:
            from .conflicts import Severity, check_logistics_item

            errors = [c for c in check_logistics_item(self) if c.severity == Severity.ERROR]
            if errors:
                raise ValidationError([c.message for c in errors])
