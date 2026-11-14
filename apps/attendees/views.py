"""Pass pages: no login, the token is the credential. Responses are never
cached or indexed and send no Referer (the URL is the secret)."""

from __future__ import annotations

from datetime import UTC
from functools import wraps
from zoneinfo import ZoneInfo

from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.program.models import Session, join_url_for
from apps.publishing.exporters.base import parse_utc, room_index, speaker_index, speaker_line
from apps.publishing.exporters.ics import build_calendar

from .models import AttendeePass, JoinClick
from .services import device_hash, pass_bundle
from .timefmt import ap_range, ap_time


def private(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        response["X-Robots-Tag"] = "noindex, nofollow"
        response["Referrer-Policy"] = "no-referrer"
        return response

    return wrapper


def _pass(token: str) -> AttendeePass | None:
    p = (
        AttendeePass.objects.select_related("event__organization", "registration", "speaker")
        .filter(token=token)
        .first()
    )
    return p if p is not None and p.is_active else None


def _gone(request):
    return render(request, "attendees/inactive.html", status=404)


def _schedule(bundle: dict, p: AttendeePass) -> list[dict]:
    """[{day, slots: [{start, label, sessions: [...]}]}] in the event's zone."""
    tz = ZoneInfo(bundle["event"]["timezone"])
    rooms, speakers = room_index(bundle), speaker_index(bundle)
    show_rooms = bundle["event"].get("format") != "virtual"
    mine = p.speaker.public_id if p.speaker_id else None
    days: dict = {}
    for s in sorted(bundle["sessions"], key=lambda x: (x.get("starts_at") or "", x["title"])):
        start, end = parse_utc(s.get("starts_at")), parse_utc(s.get("ends_at"))
        if not (start and end):
            continue
        start, end = start.astimezone(tz), end.astimezone(tz)
        day = days.setdefault(start.date(), {})
        slot = day.setdefault(start, [])
        slot.append(
            {
                "title": s["title"],
                "when": ap_range(start, end),
                "join_url": s.get("live_url", ""),
                "speakers": speaker_line(s, speakers),
                "room": rooms.get(s.get("room_id"), {}).get("name", "") if show_rooms else "",
                "canceled": s.get("canceled"),
                "mine": bool(mine and any(x["speaker_id"] == mine for x in s.get("speakers", []))),
            }
        )
    return [
        {
            "day": d,
            "slots": [
                {"start_utc": st.astimezone(UTC).isoformat(), "label": ap_time(st), "sessions": ss}
                for st, ss in slots.items()
            ],
        }
        for d, slots in days.items()
    ]


@private
def home(request, token):
    p = _pass(token)
    if p is None:
        return _gone(request)
    p.touch()
    bundle = pass_bundle(p, request)
    event = p.event
    return render(
        request,
        "attendees/home.html",
        {
            "pass": p,
            "event": event,
            "days": _schedule(bundle, p),
            "tz_abbr": event.starts_at.astimezone(event.tz).strftime("%Z") if event.starts_at else event.timezone,
            "calendar_url": request.build_absolute_uri(reverse("attendees:calendar", args=[p.token])),
        },
    )


@private
def join(request, token, public_id):
    p = _pass(token)
    if p is None:
        return _gone(request)
    event = p.event
    # Only published sessions: a pass must not unlock links for drafts.
    session = (
        Session.objects.ready()
        .filter(event=event, public_id=public_id)
        .select_related("room__room", "virtual_meeting")
        .first()
        if event.publish_schedule
        else None
    )
    url = join_url_for(session) if session else ""
    if not url:
        return render(request, "attendees/no_link.html", {"pass": p, "event": event, "session": session}, status=404)
    JoinClick.objects.create(attendee_pass=p, session=session, device=device_hash(request))
    p.touch()
    return redirect(url)


@private
def calendar(request, token):
    p = _pass(token)
    if p is None:
        return HttpResponse("This calendar link is no longer active.", status=404, content_type="text/plain")
    p.touch()
    bundle = pass_bundle(p, request)
    host = request.get_host().split(":")[0]
    body = build_calendar(bundle, bundle["sessions"], bundle["event"]["name"], host)
    return HttpResponse(body, content_type="text/calendar; charset=utf-8")
