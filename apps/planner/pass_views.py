"""Staff pages for attendee links and the run of show.

Both show private data (registrant emails; every join link and Zoom host),
so they need the STAFF role. The CSV is for a mail merge: one row per
active pass with the person's private schedule link.
"""

from __future__ import annotations

import csv
from collections import Counter
from urllib.parse import urlencode

from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.attendees.models import AttendeePass, JoinClick
from apps.attendees.services import issue_passes, pass_url
from apps.integrations.base import Capability
from apps.integrations.meetings import meeting_plan, test_setup
from apps.integrations.models import SyncRun
from apps.integrations.services import enabled_connector
from apps.integrations.tasks import pull_event_attendance, sync_event_meetings
from apps.organizations.permissions import has_role
from apps.program.models import Session, VirtualMeeting, join_url_for

from .access import Role, event_view


@event_view(Role.STAFF)
def passes(request, event):
    q = request.GET.get("q", "").strip()
    qs = (
        AttendeePass.objects.filter(event=event)
        .select_related("registration")
        .annotate(n_joins=Count("clicks"), n_devices=Count("clicks__device", distinct=True))
    )
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q))
    rows = list(qs.order_by("kind", "name"))
    all_passes = AttendeePass.objects.filter(event=event).select_related("registration")
    active = [p for p in all_passes if p.is_active]
    stats = {
        "active": len(active),
        "speakers": sum(p.kind == AttendeePass.Kind.SPEAKER for p in active),
        "used": sum(bool(p.first_used_at) for p in active),
        "no_email": sum(not p.email for p in active),
        "inactive": len(all_passes) - len(active),
        "registrations": event.registrations.filter(canceled_at__isnull=True).count(),
    }
    return render(
        request,
        "planner/passes.html",
        {"event": event, "rows": rows, "stats": stats, "q": q, "message": request.GET.get("msg", "")},
    )


def _back(event, msg: str):
    url = reverse("planner:passes", args=[event.organization.slug, event.slug])
    return redirect(f"{url}?{urlencode({'msg': msg})}")


@event_view(Role.STAFF)
@require_POST
def passes_issue(request, event):
    result = issue_passes(event)
    msg = f"Issued {result.created} new link{'s' if result.created != 1 else ''}."
    return _back(event, msg)


@event_view(Role.STAFF)
def passes_csv(request, event):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{event.slug}-attendee-links.csv"'
    response["Cache-Control"] = "private, no-store"
    writer = csv.writer(response)
    writer.writerow(["name", "email", "kind", "link", "first_name", "last_name"])
    for p in AttendeePass.objects.filter(event=event).select_related("registration").order_by("kind", "name"):
        if not p.is_active:
            continue
        reg = p.registration
        first, _, last = p.name.partition(" ")
        if reg is not None:
            first, last = reg.first_name, reg.last_name
        writer.writerow([p.name, p.email, p.kind, pass_url(p, request), first, last])
    return response


@event_view(Role.STAFF)
@require_POST
def pass_action(request, event, pk):
    p = get_object_or_404(AttendeePass, pk=pk, event=event)
    action = request.POST.get("action")
    if action == "revoke":
        p.revoked_at = timezone.now()
        p.save(update_fields=["revoked_at", "updated_at"])
        msg = f"Revoked {p.name}'s link."
    elif action == "reissue":
        p.reissue()
        msg = f"New link for {p.name}: send it from the CSV or copy it below."
    else:
        msg = "Unknown action."
    return _back(event, msg)


@event_view(Role.STAFF)
def run_of_show(request, event):
    """Every session with its room, Zoom host, join link and meeting state:
    the staff/host/captioner view the old join-link doc used to be."""
    return _run_of_show(request, event)


def _run_of_show(request, event, *, checks=None, message=""):
    tz = event.tz
    sessions = list(
        Session.objects.filter(event=event, starts_at__isnull=False)
        .select_related("room__room", "virtual_meeting")
        .prefetch_related("session_speakers__speaker")
        .order_by("starts_at", "room__sort_order", "title")
    )
    clicks = Counter(JoinClick.objects.filter(session__event=event).values_list("session_id", flat=True))
    days: dict = {}
    for s in sessions:
        meeting = getattr(s, "virtual_meeting", None)
        room = s.room.room if s.room_id else None
        if meeting is not None:
            source, stale = "meeting", meeting.needs_sync()
        elif s.live_url:
            source, stale = "manual", False
        elif room and room.join_url:
            source, stale = "standing", False
        else:
            source, stale = "", False
        start = s.starts_at.astimezone(tz)
        days.setdefault(start.date(), []).append(
            {
                "s": s,
                "start": start,
                "end": s.ends_at.astimezone(tz) if s.ends_at else None,
                "room": s.room.name if s.room_id else "",
                "host": room.host_account if room else "",
                "meeting_type": (room.meeting_type if room else "") or "",
                "join_url": join_url_for(s),
                "source": source,
                "stale": stale,
                "meeting": meeting,
                "clicks": clicks.get(s.pk, 0),
                "speakers": ", ".join(ss.speaker.full_name for ss in s.session_speakers.all()),
            }
        )
    connector, _ = enabled_connector(event.organization, Capability.VIRTUAL_SESSIONS, event, prefer="zoom")
    runs = SyncRun.objects.filter(event=event, operation__in=["sync_meetings", "pull_attendance"]).order_by("-pk")
    return render(
        request,
        "planner/run_of_show.html",
        {
            "event": event,
            "days": days.items(),
            "zoom_connected": connector is not None,
            "plan": meeting_plan(event),
            "recent_runs": [
                (label, run)
                for label, op in (("meeting sync", "sync_meetings"), ("attendance pull", "pull_attendance"))
                if (run := runs.filter(operation=op).first())
            ],
            "message": message or request.GET.get("msg", ""),
            "is_admin": has_role(request.user, event.organization, Role.ADMIN),
            "checks": checks,
            "existing_meetings": VirtualMeeting.objects.filter(session__event=event).count(),
        },
    )


@event_view(Role.STAFF)
@require_POST
def zoom_action(request, event):
    """Turn Zoom on/off for the event, test the setup (inline), or queue a
    meeting sync / attendance pull (Django Tasks; needs `db_worker`)."""
    action = request.POST.get("action")
    if action in ("on", "off"):
        event.virtual_meetings = action == "on"
        event.save(update_fields=["virtual_meetings", "updated_at"])
        msg = f"Zoom meetings turned {action} for {event.name}."
    elif action == "test":
        checks = test_setup(event, write_test=bool(request.POST.get("write_test")))
        return _run_of_show(request, event, checks=checks)
    elif not event.virtual_meetings:
        msg = "Zoom meetings are off for this event. Turn them on first."
    elif action == "attendance":
        pull_event_attendance.enqueue(event.pk, request.user.pk)
        msg = "Pulling attendance from Zoom. Reload in a minute to see counts."
    else:
        sync_event_meetings.enqueue(event.pk, request.user.pk)
        msg = "Creating and updating Zoom meetings. Reload in a minute to see the result."
    url = reverse("planner:run-of-show", args=[event.organization.slug, event.slug])
    return redirect(f"{url}?{urlencode({'msg': msg})}")


@event_view(Role.STAFF)
@require_POST
def room_count(request, event, pk):
    """Enter a session's actual room count from the run of show (htmx, saves on change)."""
    session = get_object_or_404(Session, pk=pk, event=event)
    raw = request.POST.get("room_count", "").strip()
    if raw and not raw.isdigit():
        return HttpResponse('<span class="text-xs text-red-700">Whole number</span>', status=422)
    session.room_count = int(raw) if raw else None
    session.save(update_fields=["room_count", "updated_at"])
    return HttpResponse('<span class="text-xs text-green-700" role="status">Saved</span>')
