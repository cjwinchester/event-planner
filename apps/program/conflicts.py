"""Scheduling conflict detection for sessions and logistics items.

Rules
-----
ERROR (blocks saving / placing):
  * Room overbook — two items overlap in time and their rooms share an
    *atom* (a combined room "E-F" occupies E and F; see venues.Room.atoms).
    Applies to sessions and to logistics items that block the room.
    Non-blocking logistics (water service, catering) never conflict.
  * Speaker conflict — a speaker is on two overlapping sessions.

WARNING (shown in the grid, never blocks):
  * Plenary overlap — a session overlaps an all-attendee (plenary) session.
  * Over capacity — expected headcount / seat limit exceeds room capacity.

"Overlap" is half-open: [start, end) — back-to-back items don't conflict.
Canceled and unplaced (no room/time) sessions are ignored.

Entry points
------------
  check_session(session, **proposed)        → conflicts for one session
  check_logistics_item(item, **proposed)    → conflicts for one logistics item
  event_conflicts(event)                    → every conflict in an event (grid)
Pass `starts_at=`, `ends_at=`, `room=`, `speaker_ids=` to preview a move
without saving it (the planner's drag-and-drop uses this).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from django.db.models import Q

_UNSET = object()


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class Kind(StrEnum):
    ROOM = "room_overbook"
    SPEAKER = "speaker_conflict"
    PLENARY = "plenary_overlap"
    CAPACITY = "over_capacity"


@dataclass(frozen=True)
class ItemRef:
    type: str  # "session" | "logistics"
    id: int
    title: str

    def to_dict(self) -> dict:
        return {"type": self.type, "id": self.id, "title": self.title}


@dataclass(frozen=True)
class Conflict:
    kind: Kind
    severity: Severity
    message: str
    items: tuple[ItemRef, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "kind": str(self.kind),
            "severity": str(self.severity),
            "message": self.message,
            "items": [i.to_dict() for i in self.items],
        }


# ---------------------------------------------------------------- helpers


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def atoms_for_event_rooms(event_room_ids: Iterable[int]) -> dict[int, frozenset[int]]:
    """EventRoom id → frozenset of indivisible venue Room ids it occupies."""
    from apps.events.models import EventRoom
    from apps.venues.models import Room

    ids = {i for i in event_room_ids if i is not None}
    if not ids:
        return {}
    er_to_room = dict(EventRoom.objects.filter(pk__in=ids).values_list("pk", "room_id"))
    parts: dict[int, set[int]] = defaultdict(set)
    for combo_id, part_id in Room.parts.through.objects.filter(from_room_id__in=er_to_room.values()).values_list(
        "from_room_id", "to_room_id"
    ):
        parts[combo_id].add(part_id)
    return {er: frozenset(parts.get(room_id) or {room_id}) for er, room_id in er_to_room.items()}


def _session_ref(s) -> ItemRef:
    return ItemRef("session", s.pk, s.title)


def _logistics_ref(item) -> ItemRef:
    return ItemRef("logistics", item.pk, item.title)


def _fmt(dt: datetime, tz) -> str:
    return dt.astimezone(tz).strftime("%a %-I:%M %p")


# ---------------------------------------------------------------- room checks


def room_conflicts(
    *,
    event,
    event_room_id: int,
    starts_at: datetime,
    ends_at: datetime,
    exclude: tuple[str, int | None] = ("", None),
    subject: ItemRef | None = None,
) -> list[Conflict]:
    """Blocking items that overlap [starts_at, ends_at) and share a room atom
    with `event_room_id`. `exclude` = ("session"|"logistics", pk) to skip self."""
    from .models import LogisticsItem, Session

    sessions = (
        Session.objects.active()
        .filter(event=event, room__isnull=False, starts_at__lt=ends_at, ends_at__gt=starts_at)
        .only("pk", "title", "room_id", "starts_at", "ends_at")
    )
    logistics = (
        LogisticsItem.objects.filter(event=event, room__isnull=False, starts_at__lt=ends_at, ends_at__gt=starts_at)
        .filter(Q(blocks_room=True) | Q(blocks_room__isnull=True, category__blocks_room=True))
        .only("pk", "title", "room_id", "starts_at", "ends_at")
    )
    if exclude[0] == "session" and exclude[1]:
        sessions = sessions.exclude(pk=exclude[1])
    if exclude[0] == "logistics" and exclude[1]:
        logistics = logistics.exclude(pk=exclude[1])

    sessions, logistics = list(sessions), list(logistics)
    atoms = atoms_for_event_rooms([event_room_id, *(s.room_id for s in sessions), *(i.room_id for i in logistics)])
    mine = atoms.get(event_room_id, frozenset())
    tz = event.tz
    found = []
    for other, ref in [(s, _session_ref(s)) for s in sessions] + [(i, _logistics_ref(i)) for i in logistics]:
        if atoms.get(other.room_id, frozenset()) & mine:
            found.append(
                Conflict(
                    Kind.ROOM,
                    Severity.ERROR,
                    f'Room is already booked by "{other.title}" '
                    f"({_fmt(other.starts_at, tz)}–{_fmt(other.ends_at, tz)}).",
                    tuple(r for r in (subject, ref) if r),
                )
            )
    return found


# ---------------------------------------------------------------- speaker checks


def speaker_conflicts(
    *,
    event_id: int,
    speaker_ids: Iterable[int],
    starts_at: datetime,
    ends_at: datetime,
    exclude_session_id: int | None = None,
    subject: ItemRef | None = None,
) -> list[Conflict]:
    """Other active, placed sessions in the event that share a speaker and overlap."""
    from .models import Session, SessionSpeaker

    speaker_ids = list(speaker_ids)
    if not speaker_ids:
        return []
    rows = (
        SessionSpeaker.objects.filter(
            speaker_id__in=speaker_ids,
            session__event_id=event_id,
            session__starts_at__lt=ends_at,
            session__ends_at__gt=starts_at,
            session__room__isnull=False,
        )
        .exclude(session__status=Session.Status.CANCELED)
        .select_related("speaker", "session")
    )
    if exclude_session_id:
        rows = rows.exclude(session_id=exclude_session_id)
    found = []
    for row in rows:
        found.append(
            Conflict(
                Kind.SPEAKER,
                Severity.ERROR,
                f'{row.speaker} is already on "{row.session.title}" at an overlapping time.',
                tuple(r for r in (subject, _session_ref(row.session)) if r),
            )
        )
    return found


# ---------------------------------------------------------------- public API


def check_session(
    session,
    *,
    starts_at=_UNSET,
    ends_at=_UNSET,
    room=_UNSET,
    speaker_ids=_UNSET,
) -> list[Conflict]:
    """All conflicts for `session`, optionally at a proposed room/time.

    `room` is an EventRoom (or None to unplace). `speaker_ids` defaults to the
    session's current speakers (empty for an unsaved session).
    """
    from .models import Session

    starts_at = session.starts_at if starts_at is _UNSET else starts_at
    ends_at = session.ends_at if ends_at is _UNSET else ends_at
    room_id = session.room_id if room is _UNSET else (room.pk if room else None)
    if speaker_ids is _UNSET:
        speaker_ids = list(session.session_speakers.values_list("speaker_id", flat=True)) if session.pk else []

    if session.status == Session.Status.CANCELED or not (starts_at and ends_at):
        return []
    event = session.event
    me = _session_ref(session) if session.pk else ItemRef("session", 0, session.title)
    conflicts: list[Conflict] = []
    if room_id:
        conflicts += room_conflicts(
            event=event,
            event_room_id=room_id,
            starts_at=starts_at,
            ends_at=ends_at,
            exclude=("session", session.pk),
            subject=me,
        )
        conflicts += _capacity_warnings(session, room_id, me)
    conflicts += speaker_conflicts(
        event_id=event.pk,
        speaker_ids=speaker_ids,
        starts_at=starts_at,
        ends_at=ends_at,
        exclude_session_id=session.pk,
        subject=me,
    )
    conflicts += _plenary_warnings(session, starts_at, ends_at, me)
    return conflicts


def check_logistics_item(item, *, starts_at=_UNSET, ends_at=_UNSET, room=_UNSET) -> list[Conflict]:
    """Room conflicts for a logistics item (none if it doesn't block the room)."""
    starts_at = item.starts_at if starts_at is _UNSET else starts_at
    ends_at = item.ends_at if ends_at is _UNSET else ends_at
    room_id = item.room_id if room is _UNSET else (room.pk if room else None)
    if not (starts_at and ends_at and room_id) or not item.effective_blocks_room:
        return []
    me = _logistics_ref(item) if item.pk else ItemRef("logistics", 0, item.title)
    return room_conflicts(
        event=item.event,
        event_room_id=room_id,
        starts_at=starts_at,
        ends_at=ends_at,
        exclude=("logistics", item.pk),
        subject=me,
    )


def _capacity_warnings(session, room_id: int, me: ItemRef) -> list[Conflict]:
    from apps.events.models import EventRoom

    wanted = max(filter(None, [session.expected_headcount, session.capacity]), default=None)
    if not wanted:
        return []
    room = EventRoom.objects.select_related("room").filter(pk=room_id).first()
    cap = room.effective_capacity if room else None
    if cap is not None and wanted > cap:
        return [
            Conflict(
                Kind.CAPACITY,
                Severity.WARNING,
                f"Expects {wanted} people but {room.name} holds {cap}.",
                (me,),
            )
        ]
    return []


def _plenary_warnings(session, starts_at, ends_at, me: ItemRef) -> list[Conflict]:
    from .models import Session

    others = Session.objects.scheduled().filter(event_id=session.event_id, starts_at__lt=ends_at, ends_at__gt=starts_at)
    if session.pk:
        others = others.exclude(pk=session.pk)
    if not session.is_plenary:
        others = others.filter(is_plenary=True)
    return [
        Conflict(
            Kind.PLENARY,
            Severity.WARNING,
            f'Overlaps plenary session "{o.title}".' if o.is_plenary else f'Plenary overlaps "{o.title}".',
            (me, _session_ref(o)),
        )
        for o in others.only("pk", "title", "is_plenary")
    ]


def event_conflicts(event) -> list[Conflict]:
    """Every conflict in an event — what the planning grid highlights.

    One pass over placed items sorted by start time; each overlapping pair is
    reported once.
    """
    from .models import LogisticsItem, Session, SessionSpeaker

    sessions = list(Session.objects.scheduled().filter(event=event).order_by("starts_at"))
    logistics = [
        i
        for i in LogisticsItem.objects.filter(event=event, room__isnull=False, starts_at__isnull=False)
        .select_related("category")
        .order_by("starts_at")
        if i.effective_blocks_room
    ]
    speakers_by_session: dict[int, set[int]] = defaultdict(set)
    names: dict[int, str] = {}
    for row in SessionSpeaker.objects.filter(session__in=sessions).select_related("speaker"):
        speakers_by_session[row.session_id].add(row.speaker_id)
        names[row.speaker_id] = str(row.speaker)

    atoms = atoms_for_event_rooms([s.room_id for s in sessions] + [i.room_id for i in logistics])
    items = sorted(
        [("session", s) for s in sessions] + [("logistics", i) for i in logistics],
        key=lambda pair: pair[1].starts_at,
    )
    tz = event.tz
    found: list[Conflict] = []
    for idx, (a_type, a) in enumerate(items):
        a_ref = _session_ref(a) if a_type == "session" else _logistics_ref(a)
        for b_type, b in items[idx + 1 :]:
            if b.starts_at >= a.ends_at:
                break  # sorted by start: nothing later can overlap `a`
            if not _overlaps(a.starts_at, a.ends_at, b.starts_at, b.ends_at):
                continue
            b_ref = _session_ref(b) if b_type == "session" else _logistics_ref(b)
            if atoms.get(a.room_id, frozenset()) & atoms.get(b.room_id, frozenset()):
                found.append(
                    Conflict(
                        Kind.ROOM,
                        Severity.ERROR,
                        f'"{a.title}" and "{b.title}" share a room at {_fmt(max(a.starts_at, b.starts_at), tz)}.',
                        (a_ref, b_ref),
                    )
                )
            if a_type == b_type == "session":
                shared = speakers_by_session[a.pk] & speakers_by_session[b.pk]
                for speaker_id in sorted(shared):
                    found.append(
                        Conflict(
                            Kind.SPEAKER,
                            Severity.ERROR,
                            f'{names[speaker_id]} is on both "{a.title}" and "{b.title}".',
                            (a_ref, b_ref),
                        )
                    )
                if a.is_plenary or b.is_plenary:
                    plenary, other = (a, b) if a.is_plenary else (b, a)
                    found.append(
                        Conflict(
                            Kind.PLENARY,
                            Severity.WARNING,
                            f'"{other.title}" overlaps plenary "{plenary.title}".',
                            (a_ref, b_ref),
                        )
                    )
    for s in sessions:
        found += _capacity_warnings(s, s.room_id, _session_ref(s))
    return found
