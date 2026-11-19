"""Staff review of what speakers did in the portal (edits, confirmations,
withdrawals), and the event's speaker-edit deadline."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.program.models import SessionSpeaker, SpeakerUpdate

from .access import Role, event_view


class DeadlineForm(forms.Form):
    deadline = forms.DateTimeField(
        required=False,
        label="Speakers may edit session details until",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "input"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"],
    )


def _back(event, **params):
    url = reverse("planner:speaker-updates", args=[event.organization.slug, event.slug])
    return redirect(f"{url}?{urlencode(params)}" if params else url)


@event_view(Role.STAFF)
def speaker_updates(request, event):
    show_all = request.GET.get("all") == "1"
    qs = SpeakerUpdate.objects.filter(event=event).select_related("speaker", "session", "reviewed_by")
    if not show_all:
        qs = qs.filter(reviewed_at__isnull=True)
    deadline = event.speaker_edit_deadline
    form = DeadlineForm(initial={"deadline": deadline.astimezone(event.tz).replace(tzinfo=None) if deadline else None})
    unconfirmed = (
        SessionSpeaker.objects.filter(session__event=event, is_confirmed=False)
        .exclude(session__status="canceled")
        .select_related("speaker", "session")
        .order_by("session__title")
    )
    return render(
        request,
        "planner/speaker_updates.html",
        {
            "event": event,
            "updates": qs[:300],
            "show_all": show_all,
            "form": form,
            "deadline": deadline,
            "deadline_open": bool(deadline and timezone.now() <= deadline),
            "unconfirmed": unconfirmed,
        },
    )


@event_view(Role.STAFF)
@require_POST
def speaker_updates_review(request, event):
    qs = SpeakerUpdate.objects.filter(event=event, reviewed_at__isnull=True)
    if request.POST.get("id"):
        qs = qs.filter(pk=request.POST["id"])
    n = qs.update(reviewed_at=timezone.now(), reviewed_by=request.user)
    messages.success(request, f"Marked {n} update{'s' if n != 1 else ''} reviewed.")
    return _back(event)


@event_view(Role.STAFF)
@require_POST
def speaker_deadline(request, event):
    form = DeadlineForm(request.POST)
    if form.is_valid():
        value: datetime | None = form.cleaned_data["deadline"]
        # Entered as event-local wall-clock time.
        event.speaker_edit_deadline = value.replace(tzinfo=event.tz) if value else None
        event.save(update_fields=["speaker_edit_deadline", "updated_at"])
        messages.success(request, "Speaker edits are now closed." if not value else "Deadline saved.")
    else:
        messages.error(request, "Enter a date and time.")
    return _back(event)
