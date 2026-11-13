"""Planning-grid payload and change detection (served by the API's
`events/{id}/grid/` and `events/{id}/grid-version/`).

The payload carries enough for the client to check conflicts instantly while
dragging (room atoms, speaker ids, blocking flags); the server stays the
authority on drop (program.scheduling).
"""

from __future__ import annotations

import hashlib

from django.db.models import Count, Max

from apps.events.models import EventRoom
from apps.venues.models import RoomCapacity

from .conflicts import atoms_for_event_rooms, event_conflicts
from .models import LogisticsItem, Session, TimeSlot

DEFAULT_SESSION_MINUTES = 60
SETUP_LABELS = dict(RoomCapacity.Setup.choices)


def grid_version(event) -> str:
    """Changes whenever anything shown on the grid changes."""
    parts = []
    for qs in (
        Session.objects.filter(event=event),
        LogisticsItem.objects.filter(event=event),
    ):
        agg = qs.aggregate(n=Count("pk"), m=Max("updated_at"))
        parts.append(f"{agg['n']}:{agg['m']}")
    parts.append(repr(list(TimeSlot.objects.filter(event=event).values_list("pk", "starts_at", "ends_at", "label"))))
    parts.append(repr(list(EventRoom.objects.filter(event=event).values_list("pk", "sort_order", "is_schedulable"))))
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def grid_payload(event) -> dict:
    rooms = list(event.event_rooms.filter(is_schedulable=True).select_related("room").order_by("sort_order", "pk"))
    atoms = atoms_for_event_rooms(r.pk for r in rooms)
    sessions = list(
        Session.objects.active()
        .filter(event=event)
        .select_related("session_type", "owner")
        .prefetch_related("tracks", "session_speakers__speaker")
        .order_by("starts_at", "title")
    )
    logistics = list(LogisticsItem.objects.filter(event=event).select_related("category").order_by("starts_at"))
    tracks = list(event.tracks.order_by("sort_order", "name"))
    conflicts = event_conflicts(event)

    def session_item(x):
        return {
            "type": "session",
            "id": x.pk,
            "public_id": x.public_id,
            "title": x.title,
            "room": x.room_id,
            "starts_at": x.starts_at,
            "ends_at": x.ends_at,
            "status": x.status,
            "title_status": x.title_status,
            "is_ready": x.is_ready,
            "is_plenary": x.is_plenary,
            "blocks_room": True,
            "session_type": x.session_type.name if x.session_type else "",
            "type_color": x.session_type.color if x.session_type else "",
            "default_minutes": (x.session_type.default_duration_minutes if x.session_type else None)
            or DEFAULT_SESSION_MINUTES,
            "tracks": [t.slug for t in x.tracks.all()],
            "speakers": [{"id": ss.speaker_id, "name": ss.speaker.full_name} for ss in x.session_speakers.all()],
            "owner": x.owner_id,
            "owner_name": str(x.owner) if x.owner_id else "",
            "expected_headcount": x.expected_headcount,
            "updated_at": x.updated_at,
        }

    def logistics_item(x):
        return {
            "type": "logistics",
            "id": x.pk,
            "title": x.title,
            "category": x.category.name,
            "type_color": x.category.color,
            "room": x.room_id,
            "location_note": x.location_note,
            "starts_at": x.starts_at,
            "ends_at": x.ends_at,
            "blocks_room": x.effective_blocks_room,
            "is_public": x.effective_is_public,
            "default_minutes": DEFAULT_SESSION_MINUTES,
            "tracks": [],
            "speakers": [],
            "owner": x.owner_id,
            "updated_at": x.updated_at,
        }

    items = [session_item(x) for x in sessions] + [logistics_item(x) for x in logistics]
    placed = [i for i in items if i["room"] and i["starts_at"] and i["ends_at"]]
    return {
        "event": {
            "id": event.pk,
            "name": event.name,
            "slug": event.slug,
            "organization": event.organization.slug,
            "timezone": event.timezone,
            "days": [d.isoformat() for d in event.days],
        },
        "rooms": [
            {
                "id": r.pk,
                "name": r.name,
                "sort_order": r.sort_order,
                "capacity": r.effective_capacity,
                "setup": SETUP_LABELS.get(r.setup, r.setup),
                "computer_os": r.computer_os,
                "is_recorded": r.is_recorded,
                "atoms": sorted(atoms.get(r.pk, {r.room_id})),
            }
            for r in rooms
        ],
        "time_slots": [
            {"id": t.pk, "starts_at": t.starts_at, "ends_at": t.ends_at, "label": t.label, "kind": t.kind}
            for t in event.time_slots.all()
        ],
        "tracks": [{"slug": t.slug, "name": t.name, "color": t.color} for t in tracks],
        "items": placed,
        "unscheduled": [i for i in items if i not in placed],
        "conflicts": [c.to_dict() for c in conflicts],
        "version": grid_version(event),
    }
