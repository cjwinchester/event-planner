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
