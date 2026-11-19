"""Organization staff side of hotel links: create/revoke links, set when change
tracking starts, and (Organization admins only) mark changes done once they've
confirmed the hotel updated its boards."""

from __future__ import annotations

from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.hotel.models import HotelLink, ScheduleChange, changes_from, default_cutoff
from apps.hotel.views import change_rows
from apps.organizations.permissions import has_role
from apps.venues.models import Venue

from .access import Role, event_view


class LinkForm(forms.Form):
    label = forms.CharField(
        max_length=255, widget=forms.TextInput(attrs={"class": "input", "placeholder": "e.g. JW Marriott banquets"})
    )
    venue = forms.ModelChoiceField(queryset=Venue.objects.none(), required=False, empty_label="All rooms at this event")

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["venue"].queryset = Venue.objects.filter(event_links__event=event).order_by("name")
        self.fields["venue"].widget.attrs["class"] = "input"


class CutoffForm(forms.Form):
    cutoff = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "input"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"],
    )


def _back(event, **params):
    url = reverse("planner:hotel", args=[event.organization.slug, event.slug])
    return redirect(f"{url}?{urlencode(params)}" if params else url)


@event_view(Role.STAFF)
def hotel(request, event):
    custom = event.hotel_changes_from
    return render(
        request,
        "planner/hotel.html",
        {
            "event": event,
            "links": event.hotel_links.select_related("venue", "created_by"),
            "link_form": LinkForm(event=event),
            "cutoff_form": CutoffForm(
                initial={"cutoff": custom.astimezone(event.tz).replace(tzinfo=None) if custom else None}
            ),
            "cutoff": changes_from(event),
            "cutoff_is_default": custom is None,
            "default_cutoff": default_cutoff(event),
            "tracking": timezone.now() >= changes_from(event),
            "changes": change_rows(event),
            "done": change_rows(event, done=True, limit=100),
            "is_admin": has_role(request.user, event.organization, Role.ADMIN),
        },
    )


@event_view(Role.STAFF)
@require_POST
def hotel_action(request, event):
    action = request.POST.get("action")
    if action == "create":
        form = LinkForm(request.POST, event=event)
        if form.is_valid():
            link = HotelLink.objects.create(event=event, created_by=request.user, **form.cleaned_data)
            messages.success(request, f"Created a link for {link.label}. Copy it below and send it to the hotel.")
        else:
            messages.error(request, "Give the link a label.")
    elif action == "revoke":
        link = get_object_or_404(HotelLink, pk=request.POST.get("link"), event=event)
        link.revoked_at = timezone.now()
        link.save(update_fields=["revoked_at"])
        messages.success(request, f"Revoked the link for {link.label}.")
    elif action == "cutoff":
        form = CutoffForm(request.POST)
        if form.is_valid():
            value = form.cleaned_data["cutoff"]
            event.hotel_changes_from = value.replace(tzinfo=event.tz) if value else None
            event.save(update_fields=["hotel_changes_from", "updated_at"])
            messages.success(request, "Saved when change tracking starts.")
    elif action in ("done", "done_all"):
        if not has_role(request.user, event.organization, Role.ADMIN):
            messages.error(request, "Only Organization admins can mark hotel changes done.")
            return _back(event)
        qs = ScheduleChange.objects.filter(event=event, done_at=None)
        if action == "done":
            qs = qs.filter(pk=request.POST.get("change"))
        n = qs.update(done_at=timezone.now(), done_by=request.user)
        messages.success(request, f"Marked {n} change{'s' if n != 1 else ''} done.")
    return _back(event)
