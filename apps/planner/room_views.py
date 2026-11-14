"""Rooms side panel: the grid's columns for one event.

A physical event picks rooms from its venues (the shared directory). A
virtual event's rooms are the organization's own standing Zoom rooms, kept
on a private virtual venue and reused every year; adding them here attaches
that venue to the event. "Fill rooms" places timed sessions that have no
room yet (virtual events, imports) via `program.scheduling.fill_rooms`.
"""

from __future__ import annotations

from datetime import date

from django.db import transaction
from django.db.models import Count, Max
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from apps.events.models import EventRoom, EventVenue
from apps.program import scheduling
from apps.program.models import Session
from apps.venues.models import Room, Venue

from .access import Role, event_view, require_event_role
from .forms import EventRoomForm, FillRoomsForm, VirtualRoomForm


def _refresh(response: HttpResponse) -> HttpResponse:
    response["HX-Trigger"] = "grid:refresh"
    return response


def virtual_venue_for(event, *, create: bool = False) -> Venue | None:
    """The org's private virtual venue to hold Zoom rooms: the one already on
    this event, else the org's first, else (if `create`) a new one."""
    org = event.organization
    venue = (
        Venue.objects.filter(owner=org, kind=Venue.Kind.VIRTUAL, event_links__event=event).first()
        or Venue.objects.filter(owner=org, kind=Venue.Kind.VIRTUAL).order_by("pk").first()
    )
    if venue is None and create:
        venue = Venue.objects.create(
            name=f"{org.name} virtual rooms",
            kind=Venue.Kind.VIRTUAL,
            owner=org,
            created_by_organization=org,
        )
    return venue


def _available_rooms(event):
    """Rooms that could be added: the event's venues plus the org's own virtual venues."""
    venue_ids = set(event.event_venues.values_list("venue_id", flat=True))
    venue_ids |= set(
        Venue.objects.filter(owner=event.organization, kind=Venue.Kind.VIRTUAL).values_list("pk", flat=True)
    )
    return (
        Room.objects.visible_to_orgs([event.organization_id])
        .filter(venue_id__in=venue_ids, is_active=True)
        .exclude(event_rooms__event=event)
        .select_related("venue")
        .order_by("venue__kind", "venue__name", "sort_order", "name")
    )


def _attach(event, rooms) -> int:
    """Add rooms as columns after the existing ones (attaching venues as needed)."""
    next_order = (event.event_rooms.aggregate(m=Max("sort_order"))["m"] or 0) + 1
    added = 0
    with transaction.atomic():
        for room in rooms:
            venue = room.venue
            if venue.owner_id not in (None, event.organization_id):
                continue
            role = EventVenue.Role.VIRTUAL if venue.is_virtual else EventVenue.Role.PRIMARY
            EventVenue.objects.get_or_create(event=event, venue=venue, defaults={"role": role})
            _, created = EventRoom.objects.get_or_create(
                event=event, room=room, defaults={"sort_order": next_order + added}
            )
            added += created
    return added


def _panel(request, event, *, message="", error="", fill_result=None, new_form=None, status=200):
    event_rooms = list(
        event.event_rooms.select_related("room__venue")
        .annotate(n_sessions=Count("sessions", distinct=True), n_logistics=Count("logistics_items", distinct=True))
        .order_by("sort_order", "pk")
    )
    available = list(_available_rooms(event))
    standing = [r for r in available if r.venue.owner_id == event.organization_id and r.venue.is_virtual]
    unplaced = (
        Session.objects.active()
        .filter(event=event, room__isnull=True, starts_at__isnull=False, ends_at__isnull=False)
        .count()
    )
    return render(
        request,
        "planner/partials/rooms_panel.html",
        {
            "event": event,
            "event_rooms": event_rooms,
            "available": available,
            "standing": standing,
            "unplaced": unplaced,
            "can_edit": request.can_edit,
            "new_form": new_form or VirtualRoomForm(),
            "fill_form": FillRoomsForm(event=event),
            "fill_result": fill_result,
            "message": message,
            "error": error,
            "is_virtual": event.format != event.Format.IN_PERSON,
        },
        status=status,
    )


@event_view()
def rooms(request, event):
    if request.method != "POST":
        return _panel(request, event)
    require_event_role(request.user, event, Role.STAFF)
    available = _available_rooms(event)
    if request.POST.get("all_standing"):
        picked = [r for r in available if r.venue.owner_id == event.organization_id and r.venue.is_virtual]
    else:
        picked = list(available.filter(pk__in=request.POST.getlist("rooms")))
    if not picked:
        return _panel(request, event, error="Pick at least one room.", status=422)
    added = _attach(event, picked)
    return _refresh(_panel(request, event, message=f"Added {added} room{'s' if added != 1 else ''}."))


@event_view(Role.STAFF)
@require_POST
def room_new(request, event):
    """Create a standing virtual room (a Zoom user) and add it to this event."""
    venue = virtual_venue_for(event, create=True)
    form = VirtualRoomForm(request.POST, venue=venue)
    if not form.is_valid():
        return _panel(request, event, new_form=form, status=422)
    form.instance.sort_order = (venue.rooms.aggregate(m=Max("sort_order"))["m"] or 0) + 1
    room = form.save()
    _attach(event, [room])
    return _refresh(_panel(request, event, message=f"Added {room.name}."))


@event_view(Role.STAFF)
def room_edit(request, event, pk):
    er = get_object_or_404(EventRoom.objects.select_related("room__venue"), pk=pk, event=event)
    owned_virtual = er.room.venue.is_virtual and er.room.venue.owner_id == event.organization_id
    data = request.POST if request.method == "POST" else None
    form = EventRoomForm(data, instance=er, prefix="er")
    room_form = VirtualRoomForm(data, instance=er.room, prefix="room") if owned_virtual else None
    if request.method == "POST":
        if form.is_valid() and (room_form is None or room_form.is_valid()):
            with transaction.atomic():
                form.save()
                if room_form is not None:
                    room_form.save()
            return _refresh(_panel(request, event, message=f"Saved {er.name}."))
        status = 422
    else:
        status = 200
    return render(
        request,
        "planner/partials/room_form.html",
        {"event": event, "er": er, "form": form, "room_form": room_form},
        status=status,
    )


@event_view(Role.STAFF)
@require_POST
def room_move(request, event, pk):
    ordered = list(event.event_rooms.order_by("sort_order", "pk"))
    idx = next((i for i, er in enumerate(ordered) if er.pk == pk), None)
    if idx is None:
        return _panel(request, event, error="Room not found.", status=404)
    swap = idx - 1 if request.POST.get("direction") == "up" else idx + 1
    if 0 <= swap < len(ordered):
        ordered[idx], ordered[swap] = ordered[swap], ordered[idx]
    with transaction.atomic():
        for i, er in enumerate(ordered, start=1):
            if er.sort_order != i:
                er.sort_order = i
                er.save(update_fields=["sort_order"])
    return _refresh(_panel(request, event))


@event_view(Role.STAFF)
@require_POST
def room_remove(request, event, pk):
    er = get_object_or_404(EventRoom, pk=pk, event=event)
    if er.sessions.exists() or er.logistics_items.exists():
        return _panel(request, event, error=f"Move everything out of {er.name} before removing it.", status=422)
    name = er.name
    er.delete()
    return _refresh(_panel(request, event, message=f"Removed {name} from this event."))


@event_view(Role.STAFF)
@require_POST
def fill(request, event):
    form = FillRoomsForm(request.POST, event=event)
    picked = form.cleaned_data["day"] if form.is_valid() else ""
    day = date.fromisoformat(picked) if picked else None
    result = scheduling.fill_rooms(event, day=day)
    n = len(result.placed)
    message = f"Placed {n} session{'s' if n != 1 else ''}." if n else ""
    return _refresh(_panel(request, event, message=message, fill_result=result))


@event_view(Role.STAFF)
@require_POST
def unfill(request, event):
    """Undo a fill: unplace sessions still in the room the fill put them in."""
    undone = 0
    for pair in request.POST.getlist("placed"):
        session_id, _, room_id = pair.partition(":")
        session = Session.objects.filter(event=event, pk=session_id, room_id=room_id).first()
        if session is not None:
            scheduling.unplace(session)
            undone += 1
    return _refresh(_panel(request, event, message=f"Undid {undone} placements."))
