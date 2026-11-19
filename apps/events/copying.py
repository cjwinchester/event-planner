"""Start a new event from an existing one, copying only the parts staff pick.

`PARTS` is the menu the "New event" form shows. Everything copied is a
*planning template* (rooms, slots, tracks, recurring sessions, logistics,
form questions, sponsorship tiers); people, money and private records
(registrations, contracts, RFPs, sponsorships, speakers, pitches, passes)
are never copied.

Anything with a time moves by *event day*: the source's day 1 lands on the
new day 1 at the same wall-clock time in the new event's timezone, so a
conference keeps its rhythm even when the dates fall on different weekdays.
Days past the new event's length are dropped (counted as skipped).
Logistics are re-placed through `program.scheduling.place_logistics_item`
like any other scheduling write.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from django.db import transaction

PARTS: list[tuple[str, str, str]] = [
    ("details", "Details & settings",
     "Description, intro text, visibility, what to include when published, pitch instructions, registration "
     "connector. The new event starts unpublished."),
    ("rooms", "Venues & rooms", "Venues and grid columns, with each room's setup, capacity and recording settings."),
    ("time_slots", "Time slots", "Same local times on the matching event day (day 1 → day 1)."),
    ("tracks", "Tracks", "Names, colors and descriptions."),
    ("stock_sessions", "Recurring sessions",
     "Sessions marked as stock. They arrive unscheduled with titles and descriptions back to “pending”."),
    ("logistics", "Logistics",
     "Registration hours, meals, water service…, on the matching event day, in the same rooms if those are copied."),
    ("pitch_questions", "Pitch & speaker form questions", "Custom questions on the pitch and speaker-info forms."),
    ("sponsor_levels", "Sponsorship levels & exhibitor packages",
     "Tiers and packages with prices and comp tickets (not the sponsors or exhibitors themselves)."),
]  # fmt: skip
PART_KEYS = [k for k, _, _ in PARTS]

ROOM_FIELDS = [
    "display_name", "sort_order", "setup", "capacity", "computer_os", "is_recorded", "is_schedulable", "is_public",
    "notes",
]  # fmt: skip

# publish_schedule is deliberately not copied: a new event starts unpublished, or
# its sessions would go public the moment they're ready.
DETAIL_FIELDS = [
    "description", "intro_text", "visibility", "publish_speakers", "publish_sponsors",
    "publish_exhibitors", "publish_logistics", "pitch_instructions", "registration_connector",
]  # fmt: skip


def available(source) -> dict[str, int]:
    """How many of each part `source` has (0 = nothing to copy), for the form."""
    return {
        "details": 1,
        "rooms": source.event_rooms.count(),
        "time_slots": source.time_slots.count(),
        "tracks": source.tracks.count(),
        "stock_sessions": source.sessions.filter(is_stock=True).count(),
        "logistics": source.logistics_items.count(),
        "pitch_questions": source.form_questions.count(),
        "sponsor_levels": source.sponsorship_levels.count() + source.exhibitor_packages.count(),
    }


SINGULAR = {
    "venues": "venue", "rooms": "room", "tracks": "track", "time slots": "time slot",
    "time slots skipped": "time slot skipped", "recurring sessions": "recurring session",
    "logistics items": "logistics item", "form questions": "form question",
    "sponsorship levels": "sponsorship level", "exhibitor packages": "exhibitor package",
}  # fmt: skip


def describe(counts) -> str:
    """'1 venue, 19 rooms, 2 time slots skipped'"""
    return ", ".join(
        f"{n} {SINGULAR.get(what, what) if n == 1 else what}" for what, n in counts.items() if n and what != "details"
    )


def _shift(dt: datetime, source, target):
    """Move an aware datetime to the same event day/wall-clock time in `target`, or None if that day doesn't exist."""
    local = dt.astimezone(source.tz)
    index = (local.date() - source.start_date).days
    days = target.days
    if not 0 <= index < len(days):
        return None
    return target.local_datetime(days[index], local.time())


def _clone(obj, **overrides):
    """Copy a simple row (no M2M) with new values for `overrides`."""
    skip = {"id", "created_at", "updated_at", *overrides}
    data = {f.attname: getattr(obj, f.attname) for f in obj._meta.concrete_fields if f.name not in skip}
    return type(obj).objects.create(**data, **overrides)


def copy_event_parts(source, target, parts) -> Counter:
    """Copy the chosen `parts` (keys of PARTS) from `source` into `target`. Returns counts per thing copied."""
    from apps.program.copying import copy_sessions
    from apps.program.models import TimeSlot, Track
    from apps.program.scheduling import place_logistics_item

    from .models import EventRoom, EventVenue

    parts = set(parts)
    counts: Counter = Counter()
    org_id = target.organization_id
    with transaction.atomic():
        if "details" in parts:
            for f in DETAIL_FIELDS:
                setattr(target, f, getattr(source, f))
            target.save()
            counts["details"] = 1

        room_map: dict[int, EventRoom] = {}  # source EventRoom pk → target EventRoom
        if "rooms" in parts:
            for ev in source.event_venues.select_related("venue"):
                if ev.venue.owner_id not in (None, org_id):  # another org's private venue
                    continue
                _, created = EventVenue.objects.get_or_create(
                    event=target, venue=ev.venue, defaults={"role": ev.role, "sort_order": ev.sort_order}
                )
                counts["venues"] += created
            venues = set(target.event_venues.values_list("venue_id", flat=True))
            for er in source.event_rooms.select_related("room"):
                if er.room.venue_id not in venues:
                    continue
                new, created = EventRoom.objects.get_or_create(
                    event=target,
                    room=er.room,
                    defaults={f: getattr(er, f) for f in ROOM_FIELDS},
                )
                room_map[er.pk] = new
                counts["rooms"] += created

        if "tracks" in parts:
            for t in source.tracks.all():
                _, created = Track.objects.get_or_create(
                    event=target,
                    slug=t.slug,
                    defaults={
                        "name": t.name,
                        "description": t.description,
                        "color": t.color,
                        "sort_order": t.sort_order,
                    },
                )
                counts["tracks"] += created

        if "time_slots" in parts:
            for slot in source.time_slots.all():
                start, end = _shift(slot.starts_at, source, target), _shift(slot.ends_at, source, target)
                if start is None or end is None:
                    counts["time slots skipped"] += 1
                    continue
                TimeSlot.objects.create(event=target, label=slot.label, kind=slot.kind, starts_at=start, ends_at=end)
                counts["time slots"] += 1

        if "stock_sessions" in parts:
            result = copy_sessions(source, target)
            counts["recurring sessions"] += len(result.created)
            counts["tracks"] += len(result.tracks_created)

        if "logistics" in parts:
            for item in source.logistics_items.all():
                new = _clone(item, event=target, room=None, starts_at=None, ends_at=None)
                counts["logistics items"] += 1
                start = _shift(item.starts_at, source, target) if item.starts_at else None
                end = _shift(item.ends_at, source, target) if item.ends_at else None
                if start and end:
                    room = room_map.get(item.room_id)
                    if not place_logistics_item(new, room=room, starts_at=start, ends_at=end).saved:
                        place_logistics_item(new, room=None, starts_at=start, ends_at=end)

        if "pitch_questions" in parts:
            for q in source.form_questions.all():
                _clone(q, event=target)
                counts["form questions"] += 1

        if "sponsor_levels" in parts:
            for level in source.sponsorship_levels.all():
                _clone(level, event=target)
                counts["sponsorship levels"] += 1
            for pkg in source.exhibitor_packages.all():
                _clone(pkg, event=target)
                counts["exhibitor packages"] += 1
    return counts
