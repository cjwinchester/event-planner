"""Create an event from the planner, optionally copying parts of an earlier one."""

from __future__ import annotations

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils.text import slugify

from apps.events.copying import PART_KEYS, available, copy_event_parts, describe
from apps.events.models import Event, EventSeries
from apps.organizations.permissions import organization_ids_for

from .access import Role
from .forms import NewEventForm


def next_edition(text: str) -> str:
    """'NICAR 2026' → 'NICAR 2027', 'nicar-2026' → 'nicar-2027', 'NICAR26' → 'NICAR27'."""
    bumped = re.sub(r"(?<!\d)(20\d\d)(?!\d)", lambda m: str(int(m.group(1)) + 1), text)
    if bumped == text:
        bumped = re.sub(r"(?<!\d)(\d\d)$", lambda m: f"{(int(m.group(1)) + 1) % 100:02d}", text)
    return bumped


def _initial_from(source: Event) -> dict:
    have = available(source)
    return {
        "copy_from": source.pk,
        "organization": source.organization_id,
        "series": source.series_id,
        "name": next_edition(source.name),
        "short_name": next_edition(source.short_name),
        "slug": next_edition(source.slug),
        "kind": source.kind,
        "format": source.format,
        "timezone": source.timezone,
        "parts": [k for k in PART_KEYS if have[k]],  # tick only what exists
    }


@login_required
def event_new(request):
    org_ids = organization_ids_for(request.user, Role.STAFF)
    if not org_ids:
        raise PermissionDenied
    source = None
    if request.method == "POST":
        form = NewEventForm(request.POST, org_ids=org_ids)
        if form.is_valid():
            event = _create(request, form.cleaned_data)
            return redirect("planner:grid", event.organization.slug, event.slug)
        source = form.cleaned_data.get("copy_from") if hasattr(form, "cleaned_data") else None
    else:
        source = Event.objects.filter(organization_id__in=org_ids, pk=request.GET.get("copy_from") or None).first()
        initial = _initial_from(source) if source else {"timezone": "America/New_York"}
        form = NewEventForm(initial=initial, org_ids=org_ids)
    return render(
        request, "planner/event_new.html", {"form": form, "source": source, "have": available(source) if source else {}}
    )


def _create(request, data) -> Event:
    org = data["organization"]
    with transaction.atomic():
        series = data["series"]
        if not series and data["new_series"]:
            series, _ = EventSeries.objects.get_or_create(
                organization=org, slug=slugify(data["new_series"])[:50], defaults={"name": data["new_series"]}
            )
        event = Event(
            organization=org,
            series=series,
            name=data["name"],
            short_name=data["short_name"],
            slug=data["slug"],
            kind=data["kind"],
            format=data["format"],
            start_date=data["start_date"],
            end_date=data["end_date"],
            timezone=data["timezone"],
        )
        event.full_clean()
        event.save()
        counts = copy_event_parts(data["copy_from"], event, data["parts"]) if data["parts"] else {}
    summary = describe(counts)
    copied = f" Copied from {data['copy_from'].name}: {summary or 'settings only'}." if counts else ""
    messages.success(request, f"Created {event.name}.{copied}")
    return event
