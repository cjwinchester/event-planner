"""Recording board-visible schedule changes for the hotel (see models)."""

from __future__ import annotations

from datetime import UTC

from django.db import transaction
from django.utils import timezone

from .models import ScheduleChange, changes_from


def board_state(session) -> dict | None:
    """What a hotel's board readers show for `session`, or None if it isn't on them."""
    if session is None or session.is_canceled or not (session.room_id and session.starts_at and session.ends_at):
        return None
    room = session.room
    return {
        "title": session.title,
        "room": room.name,
        "room_id": room.pk,
        "venue_id": room.room.venue_id,
        "starts_at": session.starts_at.astimezone(UTC).isoformat(),
        "ends_at": session.ends_at.astimezone(UTC).isoformat(),
    }


def tracking(event) -> bool:
    return timezone.now() >= changes_from(event)


def record_change(event, session_key: int, before: dict | None, after: dict | None) -> ScheduleChange | None:
    """Fold one board change into the session's open row (create, update, or drop it if reverted)."""
    if before == after or not tracking(event):
        return None
    with transaction.atomic():
        row = (
            ScheduleChange.objects.select_for_update()
            .filter(event=event, session_key=session_key, done_at=None)
            .first()
        )
        if row is None:
            return ScheduleChange.objects.create(event=event, session_key=session_key, before=before, after=after)
        if row.before == after:  # back to what the boards already show: nothing to do
            row.delete()
            return None
        row.after = after
        row.save(update_fields=["after", "updated_at"])
        return row
