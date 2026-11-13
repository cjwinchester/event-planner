"""Staff planning UI (phase 2): event list, the schedule grid page, and the
HTMX side panels (session quick edit/placement/speakers, time slots).

The grid itself is a client-side component (static/js/grid.js) that reads
`GET /api/v1/events/{id}/grid/` and moves things with
`POST /api/v1/{sessions|logistics}/{id}/place/`. Panels are server-rendered
partials; after a successful save they send `HX-Trigger: grid:refresh` so
the grid reloads its data.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.events.models import Event
from apps.organizations.permissions import organization_ids_for
from apps.program.conflicts import check_session
from apps.program.models import Session, SessionSpeaker, TimeSlot
from apps.program.scheduling import PlacementError, place_session
from apps.speakers.models import EventSpeaker, SpeakerProfile

from .access import Role, event_view, require_event_role
from .forms import CopySlotsForm, NewSessionForm, PlacementForm, SessionQuickForm, TimeSlotForm


def _refresh(response: HttpResponse) -> HttpResponse:
    response["HX-Trigger"] = "grid:refresh"
    return response


@login_required
def event_list(request):
    orgs = organization_ids_for(request.user)
    events = Event.objects.filter(organization_id__in=orgs).select_related("organization").order_by("-start_date")
    today = timezone.localdate()
    return render(
        request,
        "planner/event_list.html",
        {
            "upcoming": [e for e in events if e.end_date >= today][::-1],
            "past": [e for e in events if e.end_date < today],
        },
    )


@event_view()
def grid(request, event):
    config = {
        "eventId": event.pk,
        "eventName": event.name,
        "timezone": event.timezone,
        "days": [d.isoformat() for d in event.days],
        "canEdit": request.can_edit,
        "userId": request.user.pk,
        "urls": {
            "grid": reverse("event-grid", args=[event.pk]),
            "version": reverse("event-grid-version", args=[event.pk]),
            "placeSession": "/api/v1/sessions/{id}/place/",
            "placeLogistics": "/api/v1/logistics/{id}/place/",
            "sessionPanel": reverse("planner:session-panel", args=[0]).replace("/0/", "/{id}/"),
            "newSession": reverse("planner:session-new", args=[event.organization.slug, event.slug]),
            "slots": reverse("planner:slots", args=[event.organization.slug, event.slug]),
            "admin": {
                "session": reverse("admin:program_session_change", args=[0]).replace("/0/", "/{id}/"),
                "logistics": reverse("admin:program_logisticsitem_change", args=[0]).replace("/0/", "/{id}/"),
            },
        },
    }
    return render(request, "planner/grid.html", {"event": event, "config": config})


# ------------------------------------------------------------------ session panel


def _session_for(request, pk, role=Role.VIEWER) -> Session:
    session = get_object_or_404(Session.objects.select_related("event__organization", "room__room"), pk=pk)
    require_event_role(request.user, session.event, role)
    return session


def _placement_initial(session):
    if not session.is_placed:
        return {}
    tz = session.event.tz
    start, end = session.starts_at.astimezone(tz), session.ends_at.astimezone(tz)
    return {"room": session.room_id, "day": start.date().isoformat(), "start": start.time(), "end": end.time()}


def _panel(request, session, *, form=None, placement=None, message="", errors=(), status=200):
    event = session.event
    can_edit = request.user.is_superuser or event.organization_id in organization_ids_for(request.user, Role.STAFF)
    context = {
        "session": session,
        "event": event,
        "can_edit": can_edit,
        "form": form or SessionQuickForm(instance=session, event=event),
        "placement": placement or PlacementForm(initial=_placement_initial(session), event=event),
        "speakers": session.session_speakers.select_related("speaker"),
        "conflicts": check_session(session),
        "message": message,
        "errors": errors,
        "role_choices": SessionSpeaker.Role.choices,
    }
    return render(request, "planner/partials/session_panel.html", context, status=status)


@login_required
def session_panel(request, pk):
    session = _session_for(request, pk)
    if request.method != "POST":
        return _panel(request, session)
    require_event_role(request.user, session.event, Role.STAFF)
    form = SessionQuickForm(request.POST, instance=session, event=session.event)
    if not form.is_valid():
        return _panel(request, session, form=form, status=422)
    form.save()
    return _refresh(_panel(request, session, message="Saved."))


@login_required
@require_POST
def session_place(request, pk):
    session = _session_for(request, pk, Role.STAFF)
    placement = PlacementForm(request.POST, event=session.event)
    if not placement.is_valid():
        return _panel(request, session, placement=placement, status=422)
    d = placement.cleaned_data
    try:
        result = place_session(session, room=d.get("room"), starts_at=d["starts_at"], ends_at=d["ends_at"])
    except PlacementError as exc:
        return _panel(request, session, placement=placement, errors=[str(exc)], status=422)
    session.refresh_from_db()
    if not result.ok:
        return _panel(request, session, placement=placement, errors=[c.message for c in result.errors], status=409)
    return _refresh(_panel(request, session, message="Moved." if d["starts_at"] else "Unscheduled."))


@login_required
@require_POST
def session_speaker_add(request, pk):
    session = _session_for(request, pk, Role.STAFF)
    speaker = get_object_or_404(SpeakerProfile, pk=request.POST.get("speaker"))
    row = SessionSpeaker(
        session=session,
        speaker=speaker,
        role=request.POST.get("role") or SessionSpeaker.Role.SPEAKER,
        sort_order=session.session_speakers.count(),
    )
    try:
        row.full_clean()
    except ValidationError as exc:
        return _panel(request, session, errors=exc.messages, status=409)
    with transaction.atomic():
        row.save()
        EventSpeaker.objects.get_or_create(event=session.event, speaker=speaker)
    return _refresh(_panel(request, session, message=f"Added {speaker}."))


@login_required
@require_POST
def session_speaker_remove(request, pk, row_pk):
    session = _session_for(request, pk, Role.STAFF)
    session.session_speakers.filter(pk=row_pk).delete()
    return _refresh(_panel(request, session, message="Removed."))


@login_required
def speaker_search(request, pk):
    session = _session_for(request, pk)
    q = request.GET.get("q", "").strip()
    results = []
    if len(q) >= 2:
        terms = Q()
        for part in q.split():
            terms &= Q(first_name__icontains=part) | Q(last_name__icontains=part) | Q(affiliation__icontains=part)
        results = SpeakerProfile.objects.filter(terms, merged_into__isnull=True).order_by("last_name")[:10]
    return render(request, "planner/partials/speaker_results.html", {"session": session, "results": results})


@event_view(Role.STAFF)
def session_new(request, event):
    form = NewSessionForm(request.POST or None, event=event)
    if request.method == "POST" and form.is_valid():
        session = form.save()
        return _refresh(_panel(request, session, message="Created — it's in the unscheduled pool."))
    return render(request, "planner/partials/session_new.html", {"form": form, "event": event})


# ------------------------------------------------------------------ time slots


def _slots_panel(request, event, *, day=None, form=None, copy_form=None, message="", status=200):
    tz = event.tz
    day = day or request.GET.get("day") or event.start_date.isoformat()
    slots = [s for s in event.time_slots.all() if s.starts_at.astimezone(tz).date().isoformat() == day]
    return render(
        request,
        "planner/partials/slots_panel.html",
        {
            "event": event,
            "day": day,
            "slots": [{"obj": s, "start": s.starts_at.astimezone(tz), "end": s.ends_at.astimezone(tz)} for s in slots],
            "form": form or TimeSlotForm(event=event, initial={"day": day}),
            "copy_form": copy_form or CopySlotsForm(event=event, initial={"from_day": day}),
            "can_edit": request.can_edit,
            "message": message,
        },
        status=status,
    )


@event_view()
def slots(request, event):
    if request.method != "POST":
        return _slots_panel(request, event)
    require_event_role(request.user, event, Role.STAFF)
    form = TimeSlotForm(request.POST, event=event)
    if not form.is_valid():
        return _slots_panel(request, event, day=request.POST.get("day"), form=form, status=422)
    form.save()
    return _refresh(_slots_panel(request, event, day=request.POST.get("day"), message="Slot added."))


@event_view(Role.STAFF)
def slot_edit(request, event, slot_pk):
    slot = get_object_or_404(TimeSlot, pk=slot_pk, event=event)
    form = TimeSlotForm(request.POST or None, instance=slot, event=event)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            return _refresh(_slots_panel(request, event, day=request.POST.get("day"), message="Slot updated."))
        return render(
            request, "planner/partials/slot_form.html", {"form": form, "event": event, "slot": slot}, status=422
        )
    return render(request, "planner/partials/slot_form.html", {"form": form, "event": event, "slot": slot})


@event_view(Role.STAFF)
@require_POST
def slot_delete(request, event, slot_pk):
    slot = get_object_or_404(TimeSlot, pk=slot_pk, event=event)
    day = slot.starts_at.astimezone(event.tz).date().isoformat()
    slot.delete()
    return _refresh(_slots_panel(request, event, day=day, message="Slot deleted."))


@event_view(Role.STAFF)
@require_POST
def slots_copy(request, event):
    """Copy one day's slots onto other days (same wall-clock times). Days
    that already have overlapping slots are skipped, not overwritten."""
    from datetime import date as date_cls

    copy_form = CopySlotsForm(request.POST, event=event)
    if not copy_form.is_valid():
        return _slots_panel(request, event, copy_form=copy_form, status=422)
    tz = event.tz
    source_day = date_cls.fromisoformat(copy_form.cleaned_data["from_day"])
    source = [s for s in event.time_slots.all() if s.starts_at.astimezone(tz).date() == source_day]
    created, skipped = 0, 0
    with transaction.atomic():
        for target in copy_form.cleaned_data["to_days"]:
            target_day = date_cls.fromisoformat(target)
            if target_day == source_day:
                continue
            for s in source:
                new = TimeSlot(
                    event=event,
                    label=s.label,
                    kind=s.kind,
                    starts_at=event.local_datetime(target_day, s.starts_at.astimezone(tz).time()),
                    ends_at=event.local_datetime(target_day, s.ends_at.astimezone(tz).time()),
                )
                try:
                    new.full_clean()
                except ValidationError:
                    skipped += 1
                    continue
                new.save()
                created += 1
    message = f"Copied {created} slots" + (f" ({skipped} skipped: overlapping existing slots)." if skipped else ".")
    return _refresh(_slots_panel(request, event, day=source_day.isoformat(), message=message))
