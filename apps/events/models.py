"""Event series, events, and the org-private commercial side of an event.

- `EventSeries` groups recurring events (NICAR 2025, NICAR 2026, ...) for
  multi-year reporting (registration pace, attendance, locations).
- `Event` is one conference/workshop/webinar. Its `visibility` decides
  whether it appears on the shared cross-organization calendar.
- `EventVenue` attaches one or more venues (hybrid = physical + virtual).
- `EventRoom` is a venue room *as configured for this event*: grid column
  order, setup style, capacity, lab OS, recording — the per-room header
  row of the old SuperGrid.
- `EventContract` + `RoomPickupSnapshot` hold hotel contract terms and
  room-block pickup over time. ALWAYS private to the owning org: no
  visibility field, never exported, never shared.
- `Registration` / `RegistrationSnapshot` feed pace reporting and attendee
  geography. Registration rows contain personal data and are org-private.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.core.exceptions import ValidationError
from django.core.files.storage import storages
from django.db import models
from django.utils import timezone

from apps.core.models import OrgScopedManager, OrgScopedQuerySet, TimeStampedModel, Visibility
from apps.core.validators import validate_timezone


def private_storage():
    """Contracts go to the never-public storage (signed URLs on S3)."""
    return storages["private"]


class EventSeries(TimeStampedModel):
    """A recurring event family, e.g. "NICAR" or "IRE Conference"."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="event_series"
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField()
    description = models.TextField(blank=True)
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.NETWORK)

    ORG_PATH = "organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["organization", "name"]
        verbose_name_plural = "event series"
        constraints = [
            models.UniqueConstraint(fields=["organization", "slug"], name="unique_series_slug_per_org"),
        ]

    def __str__(self) -> str:
        return self.name


class EventQuerySet(OrgScopedQuerySet):
    def upcoming(self):
        return self.filter(end_date__gte=timezone.localdate()).exclude(status=Event.Status.CANCELED)

    def with_open_pitch_window(self, at: datetime | None = None):
        at = at or timezone.now()
        return self.filter(pitch_window_opens_at__lte=at, pitch_window_closes_at__gt=at)


class Event(TimeStampedModel):
    """One event: a conference (the main use case), workshop, webinar, etc."""

    class Kind(models.TextChoices):
        CONFERENCE = "conference", "Conference"
        WORKSHOP = "workshop", "Workshop / training"
        WEBINAR = "webinar", "Webinar"
        MEETUP = "meetup", "Meetup / networking"
        OTHER = "other", "Other"

    class Format(models.TextChoices):
        IN_PERSON = "in_person", "In person"
        VIRTUAL = "virtual", "Virtual"
        HYBRID = "hybrid", "Hybrid"

    class Status(models.TextChoices):
        PLANNING = "planning", "Planning (internal)"
        ANNOUNCED = "announced", "Announced"
        REGISTRATION_OPEN = "registration_open", "Registration open"
        SOLD_OUT = "sold_out", "Sold out / closed"
        COMPLETED = "completed", "Completed"
        CANCELED = "canceled", "Canceled"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="events")
    series = models.ForeignKey(EventSeries, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    name = models.CharField(max_length=255, help_text='e.g. "NICAR 2027".')
    slug = models.SlugField(help_text='Stable URL/file slug, e.g. "nicar-2027". Do not change after publishing.')
    short_name = models.CharField(max_length=64, blank=True, help_text='Hashtag-style name, e.g. "NICAR27".')
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.CONFERENCE)
    format = models.CharField(max_length=16, choices=Format.choices, default=Format.IN_PERSON)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PLANNING)
    description = models.TextField(blank=True)
    intro_text = models.TextField(blank=True, help_text="Splash-page intro for the public schedule.")
    start_date = models.DateField()
    end_date = models.DateField()
    timezone = models.CharField(max_length=64, validators=[validate_timezone], help_text="IANA name.")
    venues = models.ManyToManyField("venues.Venue", through="EventVenue", blank=True, related_name="events")
    expected_attendance = models.PositiveIntegerField(null=True, blank=True)
    banner_image = models.ImageField(upload_to="events/banners/", blank=True)

    # --- Sharing & publishing --------------------------------------------
    visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.NETWORK,
        help_text="Whether this event shows on the shared cross-organization calendar (NETWORK) "
        "or publicly (PUBLIC). Contract/cost data is never shared regardless.",
    )
    publish_schedule = models.BooleanField(default=False, help_text="Ready sessions appear in public feeds.")
    publish_speakers = models.BooleanField(default=True)
    publish_sponsors = models.BooleanField(default=True)
    publish_exhibitors = models.BooleanField(default=True)
    publish_logistics = models.BooleanField(default=True, help_text="Public logistics (registration hours...).")

    # --- Pitches ------------------------------------------------------------
    pitch_window_opens_at = models.DateTimeField(null=True, blank=True)
    pitch_window_closes_at = models.DateTimeField(null=True, blank=True)
    pitch_instructions = models.TextField(blank=True)

    # --- Registration -------------------------------------------------------
    registration_url = models.URLField(blank=True, help_text="Public registration page.")
    details_url = models.URLField(blank=True, help_text="Public event information page.")
    registration_connector = models.CharField(
        max_length=64,
        blank=True,
        help_text="Integration slug that owns registrations (e.g. 'membersuite'); see integrations app.",
    )
    additional_links = models.JSONField(
        default=list, blank=True, help_text='Extra public links: [{"label": "...", "url": "..."}].'
    )

    copied_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="copies")

    ORG_PATH = "organization"
    objects = models.Manager.from_queryset(EventQuerySet)()

    class Meta:
        ordering = ["-start_date", "name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "slug"], name="unique_event_slug_per_org"),
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")), name="event_end_after_start"
            ),
        ]
        indexes = [models.Index(fields=["start_date"]), models.Index(fields=["visibility", "start_date"])]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date must be on or after the start date."})
        opens, closes = self.pitch_window_opens_at, self.pitch_window_closes_at
        if (opens is None) != (closes is None):
            raise ValidationError("Set both pitch window dates, or neither.")
        if opens and closes and closes <= opens:
            raise ValidationError({"pitch_window_closes_at": "Pitch window must close after it opens."})
        if self.series_id and self.organization_id and self.series.organization_id != self.organization_id:
            raise ValidationError({"series": "Series belongs to a different organization."})

    # --- Time helpers ---------------------------------------------------
    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def days(self) -> list[date]:
        return [self.start_date + timedelta(days=i) for i in range((self.end_date - self.start_date).days + 1)]

    def local_datetime(self, day: date, at: time) -> datetime:
        """An aware datetime for a wall-clock time at the event."""
        return datetime.combine(day, at, tzinfo=self.tz)

    @property
    def starts_at(self) -> datetime:
        return self.local_datetime(self.start_date, time.min)

    @property
    def ends_at(self) -> datetime:
        return self.local_datetime(self.end_date + timedelta(days=1), time.min)

    def is_pitch_window_open(self, at: datetime | None = None) -> bool:
        at = at or timezone.now()
        return bool(
            self.pitch_window_opens_at
            and self.pitch_window_closes_at
            and self.pitch_window_opens_at <= at < self.pitch_window_closes_at
        )

    @property
    def is_virtual(self) -> bool:
        return self.format == self.Format.VIRTUAL

    @property
    def primary_venue(self):
        link = self.event_venues.filter(role=EventVenue.Role.PRIMARY).select_related("venue").first()
        return link.venue if link else None


class EventVenue(models.Model):
    """Links an event to a venue with a role (hybrid = PRIMARY + VIRTUAL)."""

    class Role(models.TextChoices):
        PRIMARY = "primary", "Primary venue"
        OVERFLOW = "overflow", "Overflow / additional venue"
        VIRTUAL = "virtual", "Virtual platform"
        HOTEL = "hotel", "Room-block hotel (no sessions)"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="event_venues")
    venue = models.ForeignKey("venues.Venue", on_delete=models.PROTECT, related_name="event_links")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.PRIMARY)
    sort_order = models.PositiveIntegerField(default=0)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "sort_order"]
        constraints = [models.UniqueConstraint(fields=["event", "venue"], name="unique_event_venue")]

    def __str__(self) -> str:
        return f"{self.venue} — {self.get_role_display()}"


class EventRoom(models.Model):
    """A venue room as used at one event: the schedule grid's columns.

    Sessions and logistics items are placed in EventRooms, not raw Rooms, so
    each event controls its own column order, per-event setup/capacity and
    lab details while the physical room stays shared in the venue directory.
    """

    class ComputerOS(models.TextChoices):
        NONE = "", "—"
        BYO = "byo", "Bring your own device"
        PC = "pc", "PC lab"
        MAC = "mac", "Mac lab"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="event_rooms")
    room = models.ForeignKey("venues.Room", on_delete=models.PROTECT, related_name="event_rooms")
    display_name = models.CharField(max_length=255, blank=True, help_text="Override the room's name publicly.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Grid column order.")
    setup = models.CharField(max_length=20, blank=True, help_text="RoomCapacity.Setup value for this event.")
    capacity = models.PositiveIntegerField(null=True, blank=True, help_text="Overrides the venue's chart.")
    computer_os = models.CharField(max_length=8, choices=ComputerOS.choices, blank=True)
    is_recorded = models.BooleanField(default=False, help_text="Sessions here are recorded by default.")
    is_schedulable = models.BooleanField(default=True, help_text="Show as a column in the planning grid.")
    is_public = models.BooleanField(default=True, help_text="Name the room in public feeds.")
    notes = models.TextField(blank=True)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "sort_order", "pk"]
        constraints = [models.UniqueConstraint(fields=["event", "room"], name="unique_event_room")]

    def __str__(self) -> str:
        # Dropdowns list rooms from several events; say which one.
        return f"{self.name} — {self.event.short_name or self.event.name}"

    @property
    def name(self) -> str:
        return self.display_name or self.room.name

    def clean(self):
        if self.event_id and self.room_id:
            if not self.event.event_venues.filter(venue_id=self.room.venue_id).exists():
                raise ValidationError({"room": "Add the room's venue to the event first."})

    @property
    def effective_capacity(self) -> int | None:
        if self.capacity is not None:
            return self.capacity
        if self.setup:
            cap = self.room.capacities.filter(setup=self.setup).first()
            return cap.capacity if cap else None
        return None


class EventContract(TimeStampedModel):
    """Hotel/venue contract terms for an event. ALWAYS org-private.

    The four F&B minimums mirror how contracts state them: overall vs.
    affiliate (functions run by partner groups), each with and without
    service charge & tax. Store whatever the contract states; leave the
    rest blank.
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="contracts")
    venue = models.ForeignKey("venues.Venue", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    title = models.CharField(max_length=255, blank=True, help_text='e.g. "JW Marriott group contract".')
    contract_file = models.FileField(upload_to="contracts/", storage=private_storage, blank=True)
    signed_on = models.DateField(null=True, blank=True)

    fnb_minimum_inclusive = models.DecimalField(
        "overall F&B minimum (incl. service & tax)", max_digits=12, decimal_places=2, null=True, blank=True
    )
    fnb_minimum_exclusive = models.DecimalField(
        "overall F&B minimum (excl. service & tax)", max_digits=12, decimal_places=2, null=True, blank=True
    )
    affiliate_fnb_minimum_inclusive = models.DecimalField(
        "affiliate F&B minimum (incl. service & tax)", max_digits=12, decimal_places=2, null=True, blank=True
    )
    affiliate_fnb_minimum_exclusive = models.DecimalField(
        "affiliate F&B minimum (excl. service & tax)", max_digits=12, decimal_places=2, null=True, blank=True
    )
    av_cost = models.DecimalField("AV cost", max_digits=12, decimal_places=2, null=True, blank=True)
    attendee_room_rate = models.DecimalField(
        "attendee nightly room rate", max_digits=8, decimal_places=2, null=True, blank=True
    )
    room_block_total = models.PositiveIntegerField(
        "room-night pickup requirement", null=True, blank=True, help_text="Total room nights committed."
    )
    room_block_cutoff = models.DateField(null=True, blank=True)
    pickup_tracking_url = models.URLField(
        blank=True, help_text="Where current pickup is reported (often a hotel portal requiring login)."
    )
    notes = models.TextField(blank=True)

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "pk"]

    def __str__(self) -> str:
        return self.title or f"Contract — {self.event}"

    @property
    def latest_pickup(self):
        return self.pickup_snapshots.order_by("-recorded_on").first()


class RoomPickupSnapshot(models.Model):
    """Room-block pickup as of a date — a time series, entered by hand or by
    a future connector to the hotel's reporting portal."""

    contract = models.ForeignKey(EventContract, on_delete=models.CASCADE, related_name="pickup_snapshots")
    recorded_on = models.DateField(default=timezone.localdate)
    rooms_picked_up = models.PositiveIntegerField(help_text="Cumulative room nights booked.")
    source = models.CharField(max_length=64, default="manual")
    notes = models.CharField(max_length=255, blank=True)

    ORG_PATH = "contract__event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["contract", "recorded_on"]
        constraints = [
            models.UniqueConstraint(fields=["contract", "recorded_on"], name="unique_pickup_per_day"),
        ]

    def __str__(self) -> str:
        return f"{self.recorded_on}: {self.rooms_picked_up}"


class Registration(TimeStampedModel):
    """One attendee registration, usually synced from a registration/AMS
    connector. Personal data — org-private, never exported publicly.

    `location` is a *coarse* point (postal-code or city centroid) used for
    attendee-dispersion heat maps; never store a street-level point here.
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="registrations")
    source = models.CharField(max_length=64, default="manual", help_text="Connector slug or 'manual'/'import'.")
    external_id = models.CharField(max_length=255, blank=True)
    first_name = models.CharField(max_length=255, blank=True)
    last_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    affiliation = models.CharField(max_length=255, blank=True)
    ticket_type = models.CharField(max_length=255, blank=True)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    registered_at = models.DateTimeField()
    canceled_at = models.DateTimeField(null=True, blank=True)
    member_status = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=255, blank=True)
    region = models.CharField(max_length=255, blank=True)
    postal_code = models.CharField(max_length=32, blank=True)
    country = models.CharField(max_length=2, blank=True)
    location = gis_models.PointField(geography=True, srid=4326, null=True, blank=True)
    speaker = models.ForeignKey(
        "speakers.SpeakerProfile", null=True, blank=True, on_delete=models.SET_NULL, related_name="registrations"
    )
    raw = models.JSONField(default=dict, blank=True, help_text="Connector payload, for debugging/re-mapping.")

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "registered_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "source", "external_id"],
                condition=~models.Q(external_id=""),
                name="unique_registration_external_id",
            ),
        ]
        indexes = [models.Index(fields=["event", "registered_at"])]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name} — {self.event}".strip()

    @property
    def is_active(self) -> bool:
        return self.canceled_at is None


class RegistrationSnapshot(models.Model):
    """A registration count as of a date — for pace reporting when per-person
    registration data isn't available (or for historical events)."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="registration_snapshots")
    recorded_on = models.DateField()
    total_registrations = models.PositiveIntegerField()
    source = models.CharField(max_length=64, default="manual")

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["event", "recorded_on"]
        constraints = [
            models.UniqueConstraint(fields=["event", "recorded_on"], name="unique_registration_snapshot_day"),
        ]

    def __str__(self) -> str:
        return f"{self.event}: {self.total_registrations} on {self.recorded_on}"


class EventStaffAssignment(models.Model):
    """Who on staff works on an event and in what capacity (lead, content...)."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="staff_assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_assignments")
    role = models.CharField(max_length=64, default="planner", help_text='e.g. "lead", "content", "logistics".')

    ORG_PATH = "event__organization"
    objects = OrgScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "user", "role"], name="unique_event_staff_role"),
        ]

    def __str__(self) -> str:
        return f"{self.user} — {self.role} @ {self.event}"
