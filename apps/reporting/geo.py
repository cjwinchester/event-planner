"""Geographic reports for Leaflet maps (GeoJSON FeatureCollections).

- attendee-dispersion: where an event's (or series') attendees come from,
  aggregated to a coarse grid so no individual is locatable. Feed it to
  Leaflet.heat ([lat, lon, weight]) or a choropleth.
- event-locations: every event in a series/org on a map with its year,
  for "where have we been?" venue planning.
"""

from __future__ import annotations

from collections import Counter

from apps.events.models import Event, EventVenue, Registration

from .registry import Report, ReportScope, register

GRID_DEGREES = 0.25  # ≈ 25 km cells: coarse enough to protect privacy
MIN_CELL_COUNT = 3  # suppress cells with fewer attendees


def attendee_dispersion(events) -> dict:
    cells: Counter = Counter()
    for point in Registration.objects.filter(
        event__in=events, canceled_at__isnull=True, location__isnull=False
    ).values_list("location", flat=True):
        lat = round(point.y / GRID_DEGREES) * GRID_DEGREES
        lon = round(point.x / GRID_DEGREES) * GRID_DEGREES
        cells[(round(lat, 4), round(lon, 4))] += 1
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"count": count},
        }
        for (lat, lon), count in sorted(cells.items())
        if count >= MIN_CELL_COUNT
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {"grid_degrees": GRID_DEGREES, "suppressed_below": MIN_CELL_COUNT},
    }


def event_locations(events) -> dict:
    features = []
    links = (
        EventVenue.objects.filter(event__in=events, role=EventVenue.Role.PRIMARY, venue__location__isnull=False)
        .select_related("event", "venue")
        .order_by("event__start_date")
    )
    for link in links:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [link.venue.location.x, link.venue.location.y]},
                "properties": {
                    "event": link.event.name,
                    "slug": link.event.slug,
                    "year": link.event.start_date.year,
                    "venue": link.venue.name,
                    "city": link.venue.city,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _events_for(obj):
    return obj.events.all() if not isinstance(obj, Event) else Event.objects.filter(pk=obj.pk)


@register
class AttendeeDispersionReport(Report):
    slug = "attendee-dispersion"
    title = "Attendee dispersion"
    description = "Gridded attendee home locations (GeoJSON) for heat maps."
    scope = ReportScope.SERIES

    def compute(self, obj, **params):
        return attendee_dispersion(_events_for(obj))


@register
class EventLocationsReport(Report):
    slug = "event-locations"
    title = "Event locations"
    description = "Where each event in a series was held (GeoJSON)."
    scope = ReportScope.SERIES

    def compute(self, obj, **params):
        return event_locations(_events_for(obj))
