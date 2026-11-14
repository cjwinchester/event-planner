"""The one write path for placing things on the schedule grid.

`place_session()` / `place_logistics_item()` are what the drag-and-drop
planner (via the API's `place` actions) and any other caller should use to
move an item to a room/time. They:

  1. lock the event row (SELECT ... FOR UPDATE) so two planners dragging
     at the same moment can't both pass the conflict check and double-book;
  2. run the conflict checks against the *proposed* placement;
  3. save only if there are no ERROR conflicts (warnings are returned);
  4. return a `PlacementResult` the UI can render either way.

`dry_run=True` performs the checks without saving (hover/preview).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction

from apps.events.models import Event

from .conflicts import Conflict, Severity, check_logistics_item, check_session
from .models import LogisticsItem, Session


@dataclass
class PlacementResult:
    ok: bool
    saved: bool
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def errors(self) -> list[Conflict]:
        return [c for c in self.conflicts if c.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Conflict]:
        return [c for c in self.conflicts if c.severity == Severity.WARNING]

    def to_dict(self) -> dict:
        return {"ok": self.ok, "saved": self.saved, "conflicts": [c.to_dict() for c in self.conflicts]}


class PlacementError(ValueError):
    """Bad input (room from another event, end before start...)."""


def _validate(event: Event, room, starts_at: datetime | None, ends_at: datetime | None) -> None:
    if (starts_at is None) != (ends_at is None):
        raise PlacementError("Provide both start and end, or neither.")
    if starts_at and ends_at <= starts_at:
        raise PlacementError("End must be after start.")
    if room is not None and room.event_id != event.pk:
        raise PlacementError("That room is not set up for this event.")
    if starts_at and (starts_at < event.starts_at or ends_at > event.ends_at):
        raise PlacementError("Times must fall within the event's dates.")


def place_session(
    session: Session,
    *,
    room,
    starts_at: datetime | None,
    ends_at: datetime | None,
    dry_run: bool = False,
) -> PlacementResult:
    """Move `session` to `room` (an EventRoom or None) at [starts_at, ends_at)."""
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=session.event_id)
        _validate(event, room, starts_at, ends_at)
        conflicts = check_session(session, room=room, starts_at=starts_at, ends_at=ends_at)
        result = PlacementResult(ok=not any(c.severity == Severity.ERROR for c in conflicts), saved=False)
        result.conflicts = conflicts
        if result.ok and not dry_run:
            session.room, session.starts_at, session.ends_at = room, starts_at, ends_at
            session.save(update_fields=["room", "starts_at", "ends_at", "updated_at"])
            result.saved = True
        return result


def place_logistics_item(
    item: LogisticsItem,
    *,
    room,
    starts_at: datetime | None,
    ends_at: datetime | None,
    dry_run: bool = False,
) -> PlacementResult:
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=item.event_id)
        _validate(event, room, starts_at, ends_at)
        conflicts = check_logistics_item(item, room=room, starts_at=starts_at, ends_at=ends_at)
        result = PlacementResult(ok=not any(c.severity == Severity.ERROR for c in conflicts), saved=False)
        result.conflicts = conflicts
        if result.ok and not dry_run:
            item.room, item.starts_at, item.ends_at = room, starts_at, ends_at
            item.save(update_fields=["room", "starts_at", "ends_at", "updated_at"])
            result.saved = True
        return result


def unplace(item) -> None:
    """Take an item off the grid (back to the unscheduled pool)."""
    item.room, item.starts_at, item.ends_at = None, None, None
    item.save(update_fields=["room", "starts_at", "ends_at", "updated_at"])


@dataclass
class FillResult:
    placed: list[tuple[Session, object]] = field(default_factory=list)  # (session, EventRoom)
    skipped: list[tuple[Session, str]] = field(default_factory=list)  # (session, reason)


def fill_rooms(event: Event, *, day=None) -> FillResult:
    """Put timed sessions that have no room into the first free room.

    For virtual events (rooms are interchangeable Zoom accounts) and for
    imports that know when sessions ran but not where. Each placement goes
    through `place_session`, so conflict rules are the same as a drag; a
    session that fits nowhere is reported, not forced. Rooms are tried in
    grid order with webinar rooms last (they're usually reserved for
    keynotes). Longest sessions first within a start time, so a long
    session isn't squeezed out by short ones.
    """
    from .conflicts import Kind

    rooms = sorted(
        event.event_rooms.filter(is_schedulable=True).select_related("room"),
        key=lambda er: (er.room.meeting_type == "webinar", er.sort_order, er.pk),
    )
    sessions = [
        s
        for s in Session.objects.active().filter(
            event=event, room__isnull=True, starts_at__isnull=False, ends_at__isnull=False
        )
        if day is None or s.starts_at.astimezone(event.tz).date() == day
    ]
    sessions.sort(key=lambda s: (s.starts_at, -(s.ends_at - s.starts_at), s.title))
    result = FillResult()
    if not rooms:
        result.skipped = [(s, "This event has no rooms.") for s in sessions]
        return result
    for s in sessions:
        reasons: list[str] = []
        for room in rooms:
            try:
                attempt = place_session(s, room=room, starts_at=s.starts_at, ends_at=s.ends_at)
            except PlacementError as exc:
                reasons = [str(exc)]
                break
            if attempt.saved:
                result.placed.append((s, room))
                break
            other = [c.message for c in attempt.errors if c.kind != Kind.ROOM]
            if other:  # a speaker clash follows the session into every room
                reasons = other
                break
        else:
            reasons = ["No free room at that time."]
        if reasons:
            result.skipped.append((s, " ".join(reasons)))
    return result
