"""Venues, rooms and floor plans — a *shared directory* across organizations.

Hotels and conference centers are reused year after year and by different
nonprofits, so `Venue`, `Room`, `RoomCapacity` and `Floorplan` are global
(no ORG_PATH): one org entering a hotel's rooms helps every other org that
books it ("aggregated venue insights"). What an organization learns
privately about a venue lives in the org-scoped `VenueNote` and
`VenueContact`, each with its own `visibility`.

Mapping model (Leaflet.js)
--------------------------
- `Venue.location` is a real geographic point (SRID 4326) for maps of
  where events happen.
- Each `Floorplan` is an image. Rooms are pinned on it in *fractional*
  image coordinates (0.0–1.0 from the top-left), which stay correct if the
  image is replaced by a higher-resolution copy. Render with Leaflet's
  `L.CRS.Simple` and bounds `[[0, 0], [height, width]]`:
  `latLng = [ (1 - y) * height, x * width ]` (Leaflet's y axis points up).
- Optionally, `Floorplan.geo_bounds` georeferences the image so it can be
  overlaid on a real basemap with `L.imageOverlay(url, bounds)`.

Virtual venues
--------------
A virtual event's "rooms" (Zoom Room 1, Webinar, ...) are `Room`s of a
`Venue` with `kind=VIRTUAL`; `Room.join_url` / `host_account` hold the
standing meeting link and host account. Hybrid events attach both a
physical and a virtual venue via `events.EventVenue`.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower

from apps.core.models import OrgScopedManager, TimeStampedModel, Visibility
from apps.core.validators import validate_timezone

_fraction = [MinValueValidator(0.0), MaxValueValidator(1.0)]


class Venue(TimeStampedModel):
    """A hotel, conference center, campus, or online platform."""

    class Kind(models.TextChoices):
        PHYSICAL = "physical", "Physical"
        VIRTUAL = "virtual", "Virtual / online"

    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.PHYSICAL)
    address1 = models.CharField("address line 1", max_length=255, blank=True)
    address2 = models.CharField("address line 2", max_length=255, blank=True)
    city = models.CharField(max_length=255, blank=True)
    region = models.CharField("state / region", max_length=255, blank=True)
    postal_code = models.CharField(max_length=32, blank=True)
    country = models.CharField(max_length=2, default="US", help_text="ISO 3166-1 alpha-2 code.")
    location = gis_models.PointField(
        geography=True, srid=4326, null=True, blank=True, help_text="Geocoded point (lon/lat)."
    )
    geocode_source = models.CharField(
        max_length=64, blank=True, help_text="Which geocoder produced `location` (or 'manual')."
    )
    timezone = models.CharField(max_length=64, blank=True, validators=[validate_timezone])
    website = models.URLField(blank=True)
    phone = models.CharField(max_length=64, blank=True)
    email = models.EmailField(blank=True, help_text="General venue contact (not a person).")
    maps_url = models.URLField(blank=True, help_text="Google/Apple Maps link for attendees.")
    public_access_notes = models.TextField(
        blank=True,
        help_text="Accessibility, transit, parking, entrances — published to attendees.",
    )
    created_by_organization = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Provenance only; venues are shared by every organization.",
    )

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["city", "region"])]

    def __str__(self) -> str:
        where = ", ".join(p for p in (self.city, self.region) if p)
        return f"{self.name} ({where})" if where else self.name

    @property
    def is_virtual(self) -> bool:
        return self.kind == self.Kind.VIRTUAL

    @property
    def one_line_address(self) -> str:
        parts = [self.address1, self.address2, self.city, " ".join(p for p in (self.region, self.postal_code) if p)]
        return ", ".join(p for p in parts if p)


class Floorplan(TimeStampedModel):
    """One floor-plan image for a venue (usually one per floor)."""

    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="floorplans")
    label = models.CharField(max_length=64, help_text='e.g. "Level 2", "Mezzanine".')
    level = models.IntegerField(default=0, help_text="Sort order / numeric floor (lowest first).")
    image = models.ImageField(upload_to="venues/floorplans/", width_field="width", height_field="height")
    width = models.PositiveIntegerField(null=True, blank=True, editable=False)
    height = models.PositiveIntegerField(null=True, blank=True, editable=False)
    source_file = models.FileField(
        upload_to="venues/floorplans/originals/", blank=True, help_text="Original upload (e.g. the hotel's PDF)."
    )
    caption = models.CharField(max_length=255, blank=True)
    geo_bounds = gis_models.PolygonField(
        srid=4326,
        null=True,
        blank=True,
        help_text="Optional georeference: the image's corners on a real map (for L.imageOverlay).",
    )

    class Meta:
        ordering = ["venue", "level", "label"]
        constraints = [
            models.UniqueConstraint("venue", Lower("label"), name="unique_floorplan_label_per_venue"),
        ]

    def __str__(self) -> str:
        return f"{self.venue.name} — {self.label}"

    @property
    def leaflet_bounds(self) -> list[list[int]] | None:
        """Bounds for `L.CRS.Simple`: [[0, 0], [height, width]]."""
        if not (self.width and self.height):
            return None
        return [[0, 0], [self.height, self.width]]


class Room(TimeStampedModel):
    """A bookable space at a venue (or a standing virtual meeting room).

    Combined (airwall) rooms
    ------------------------
    Hotels sell "White River E-F" as one room made of E and F. Model the
    combination as its own Room whose `parts` are E and F. Scheduling
    conflict checks compare `atoms()`: a combination occupies its parts, so
    booking "E-F" conflicts with "E", "F", and any other combination that
    contains E or F.
    """

    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="rooms")
    name = models.CharField(max_length=255)
    parts = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="combinations",
        help_text="For a combined room: the individual rooms it is made of.",
    )
    floorplan = models.ForeignKey(Floorplan, null=True, blank=True, on_delete=models.SET_NULL, related_name="rooms")
    floor_label = models.CharField(max_length=64, blank=True, help_text="Used when no floorplan is linked.")
    square_feet = models.PositiveIntegerField(null=True, blank=True)
    ceiling_height_feet = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    # Pin on the floorplan image, fractional (0–1) from the top-left corner.
    map_x = models.FloatField(null=True, blank=True, validators=_fraction)
    map_y = models.FloatField(null=True, blank=True, validators=_fraction)
    # Optional outline, fractional image coordinates: [[x, y], [x, y], ...].
    map_shape = models.JSONField(default=list, blank=True)
    accessibility_notes = models.TextField(blank=True)
    # Virtual rooms
    join_url = models.URLField(blank=True, help_text="Standing meeting link (virtual rooms).")
    host_account = models.CharField(
        max_length=255, blank=True, help_text="Host account/login for a virtual room, e.g. room1@org.org."
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["venue", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint("venue", Lower("name"), name="unique_room_name_per_venue"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.venue.name})"

    def clean(self):
        if self.floorplan_id and self.venue_id and self.floorplan.venue_id != self.venue_id:
            raise ValidationError({"floorplan": "Floorplan must belong to the same venue as the room."})
        if (self.map_x is None) != (self.map_y is None):
            raise ValidationError("Set both map_x and map_y, or neither.")
        if self.map_x is not None and not self.floorplan_id:
            raise ValidationError({"floorplan": "A map position needs a floorplan."})
        for point in self.map_shape or []:
            if not (isinstance(point, list | tuple) and len(point) == 2 and all(0 <= v <= 1 for v in point)):
                raise ValidationError({"map_shape": "Use [[x, y], ...] with fractional (0–1) coordinates."})

    @property
    def floor(self) -> str:
        return self.floorplan.label if self.floorplan_id else self.floor_label

    def atoms(self) -> frozenset[int]:
        """The indivisible room ids this room occupies (itself, or its parts)."""
        part_ids = frozenset(self.parts.values_list("pk", flat=True)) if self.pk else frozenset()
        return part_ids or frozenset({self.pk})


class RoomCapacity(models.Model):
    """Capacity of a room for one setup style (from the hotel's capacity chart)."""

    class Setup(models.TextChoices):
        THEATER = "theater", "Theater"
        CLASSROOM = "classroom", "Classroom"
        ROUNDS = "rounds", "Banquet rounds"
        CRESCENT = "crescent", "Crescent rounds"
        RECEPTION = "reception", "Reception"
        U_SHAPE = "u_shape", "U-shape"
        HOLLOW_SQUARE = "hollow_square", "Hollow square"
        BOARDROOM = "boardroom", "Boardroom"
        COMPUTER_LAB = "computer_lab", "Computer lab"
        VIRTUAL = "virtual", "Virtual (seat/licence limit)"

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="capacities")
    setup = models.CharField(max_length=20, choices=Setup.choices)
    capacity = models.PositiveIntegerField()

    class Meta:
        ordering = ["room", "setup"]
        verbose_name_plural = "room capacities"
        constraints = [models.UniqueConstraint(fields=["room", "setup"], name="unique_room_setup_capacity")]

    def __str__(self) -> str:
        return f"{self.room.name}: {self.capacity} ({self.get_setup_display()})"


class VenueContact(TimeStampedModel):
    """A person at the venue (sales manager, AV lead) — org-owned, private by default."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=255, blank=True, help_text='e.g. "Sales manager", "AV".')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.PRIVATE)

    ORG_PATH = "organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["venue", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.venue.name})"


class VenueNote(TimeStampedModel):
    """An organization's notes on a venue — "aggregated venue insights" when shared.

    Never put contract/cost figures here; those live in events.EventContract,
    which is always private.
    """

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="notes")
    event = models.ForeignKey(
        "events.Event", null=True, blank=True, on_delete=models.SET_NULL, related_name="venue_notes"
    )
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    title = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    rating = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.PRIVATE)

    ORG_PATH = "organization"
    objects = OrgScopedManager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title or f"Note on {self.venue.name}"
