"""The hotel's page: no login, the token is the credential (like attendee
passes). Never cached or indexed; no Referer."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from django.db.models import F
from django.shortcuts import render
from django.utils import timezone

from apps.attendees.views import private
from apps.program.models import Session

from .models import HotelLink, ScheduleChange, changes_from


def _link(token) -> HotelLink | None:
    link = HotelLink.objects.select_related("event__organization", "venue").filter(token=token).first()
    return link if link is not None and link.is_active else None


def _in_venue(state: dict | None, venue_id) -> bool:
    return state is not None and (venue_id is None or state.get("venue_id") == venue_id)


def _local(iso: str, tz: ZoneInfo) -> datetime:
    return datetime.fromisoformat(iso).astimezone(tz)


def _with_times(state: dict | None, tz: ZoneInfo) -> dict | None:
    if state is None:
        return None
    return {**state, "start": _local(state["starts_at"], tz), "end": _local(state["ends_at"], tz)}


def change_rows(event, venue_id=None, *, done: bool = False, limit=None):
    """Changes for the hotel (or staff): open ones with title changes first, newest first."""
    qs = ScheduleChange.objects.filter(event=event, done_at__isnull=not done)
    qs = qs.order_by("-done_at") if done else qs.order_by("-updated_at")
    tz = event.tz
    rows = []
    for c in qs[:limit] if limit else qs:
        if not (_in_venue(c.before, venue_id) or _in_venue(c.after, venue_id)):
            continue
        rows.append(
            {
                "c": c,
                "kind": c.kind,
                "fields": c.changed_fields(),
                "before": _with_times(c.before, tz),
                "after": _with_times(c.after, tz),
            }
        )
    if not done:
        rows.sort(key=lambda r: not r["c"].title_changed)  # stable: title changes first, newest first within
    return rows


def board(event, venue_id=None):
    """Every programmed time block, by day and room."""
    tz = event.tz
    sessions = (
        Session.objects.active()
        .filter(event=event, room__isnull=False, starts_at__isnull=False, ends_at__isnull=False)
        .select_related("room__room")
        .order_by("starts_at", "room__sort_order", "title")
    )
    if venue_id:
        sessions = sessions.filter(room__room__venue_id=venue_id)
    open_keys = set(ScheduleChange.objects.filter(event=event, done_at=None).values_list("session_key", flat=True))
    days: dict = {}
    for s in sessions:
        start = s.starts_at.astimezone(tz)
        rooms = days.setdefault(start.date(), {})
        rooms.setdefault((s.room.sort_order, s.room.name), []).append(
            {"s": s, "start": start, "end": s.ends_at.astimezone(tz), "changed": s.pk in open_keys}
        )
    return [
        {"day": day, "rooms": [{"name": name, "blocks": blocks} for (_, name), blocks in sorted(rooms.items())]}
        for day, rooms in sorted(days.items())
    ]


@private
def home(request, token):
    link = _link(token)
    if link is None:
        return render(request, "hotel/inactive.html", status=404)
    HotelLink.objects.filter(pk=link.pk).update(view_count=F("view_count") + 1, last_viewed_at=timezone.now())
    event = link.event
    return render(
        request,
        "hotel/home.html",
        {
            "link": link,
            "event": event,
            "changes": change_rows(event, link.venue_id),
            "done": change_rows(event, link.venue_id, done=True, limit=50),
            "days": board(event, link.venue_id),
            "tracking_from": changes_from(event),
        },
    )
