"""Forms for the planner's side panels. Times are entered as event-local
wall-clock values and converted with the event's timezone."""

from __future__ import annotations

from datetime import datetime

from django import forms

from apps.events.models import EventRoom
from apps.program.models import Session, SessionType, SkillLevel, TimeSlot, Track
from apps.venues.models import Room


def _day_choices(event):
    return [(d.isoformat(), d.strftime("%a %b %-d")) for d in event.days]


class SessionQuickForm(forms.ModelForm):
    """Quick edits from the grid's side panel. Placement is separate
    (PlacementForm) so it always goes through program.scheduling."""

    class Meta:
        model = Session
        fields = [
            "title", "title_status", "description_status", "status", "session_type", "skill_level", "tracks",
            "is_plenary", "expected_headcount", "internal_notes",
        ]  # fmt: skip
        widgets = {
            "tracks": forms.CheckboxSelectMultiple,
            "internal_notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.event = event
        org = event.organization
        self.fields["session_type"].queryset = SessionType.objects.filter(organization=org, is_active=True)
        self.fields["skill_level"].queryset = SkillLevel.objects.filter(organization=org)
        self.fields["tracks"].queryset = Track.objects.filter(event=event)


class NewSessionForm(forms.ModelForm):
    class Meta:
        model = Session
        fields = ["title", "session_type"]

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.event = event
        self.fields["session_type"].queryset = SessionType.objects.filter(organization=event.organization)


class PlacementForm(forms.Form):
    """Keyboard-friendly alternative to dragging."""

    room = forms.ModelChoiceField(queryset=EventRoom.objects.none(), required=False)
    day = forms.ChoiceField(required=False)
    start = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    end = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}))

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.fields["room"].queryset = EventRoom.objects.filter(event=event).select_related("room", "event")
        self.fields["room"].label_from_instance = lambda r: r.name
        self.fields["day"].choices = [("", "—"), *_day_choices(event)]

    def clean(self):
        data = super().clean()
        filled = [bool(data.get(k)) for k in ("room", "day", "start", "end")]
        if any(filled) and not all(filled):
            raise forms.ValidationError("Fill in room, day, start and end — or clear them all to unschedule.")
        if all(filled):
            day = datetime.fromisoformat(data["day"]).date()
            data["starts_at"] = self.event.local_datetime(day, data["start"])
            data["ends_at"] = self.event.local_datetime(day, data["end"])
        else:
            data["starts_at"] = data["ends_at"] = None
        return data


class TimeSlotForm(forms.ModelForm):
    day = forms.ChoiceField()
    start = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    end = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))

    class Meta:
        model = TimeSlot
        fields = ["label", "kind"]

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.instance.event = event
        self.fields["day"].choices = _day_choices(event)
        if self.instance.pk:
            tz = event.tz
            start, end = self.instance.starts_at.astimezone(tz), self.instance.ends_at.astimezone(tz)
            self.initial.update(day=start.date().isoformat(), start=start.time(), end=end.time())

    def clean(self):
        data = super().clean()
        if all(data.get(k) for k in ("day", "start", "end")):
            day = datetime.fromisoformat(data["day"]).date()
            self.instance.starts_at = self.event.local_datetime(day, data["start"])
            self.instance.ends_at = self.event.local_datetime(day, data["end"])
        return data


class CopySlotsForm(forms.Form):
    from_day = forms.ChoiceField()
    to_days = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["from_day"].choices = _day_choices(event)
        self.fields["to_days"].choices = _day_choices(event)


class VirtualRoomForm(forms.ModelForm):
    """A standing virtual room: one Zoom user. Shared by all of the org's events."""

    class Meta:
        model = Room
        fields = ["name", "meeting_type", "host_account", "join_url"]
        labels = {"host_account": "Zoom host (email)", "join_url": "Standing join link (optional)"}

    def __init__(self, *args, venue=None, **kwargs):
        super().__init__(*args, **kwargs)
        if venue is not None:
            self.instance.venue = venue
        self.fields["meeting_type"].required = True
        self.fields["meeting_type"].initial = Room.MeetingType.MEETING

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        venue = self.instance.venue
        clash = Room.objects.filter(venue=venue, name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("You already have a room with that name.")
        return name


class EventRoomForm(forms.ModelForm):
    """Per-event column settings."""

    class Meta:
        model = EventRoom
        fields = ["display_name", "is_recorded", "is_schedulable", "is_public"]
        labels = {
            "display_name": "Name at this event (optional)",
            "is_recorded": "Recorded",
            "is_schedulable": "Show on the grid",
            "is_public": "Name it in public schedules",
        }


class FillRoomsForm(forms.Form):
    day = forms.ChoiceField(required=False)

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["day"].choices = [("", "All days"), *_day_choices(event)]


def _timezone_choices(current: str = ""):
    from zoneinfo import available_timezones

    common = ["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles",
              "America/Anchorage", "Pacific/Honolulu", "UTC"]  # fmt: skip
    rest = sorted(z for z in available_timezones() if z not in common and "/" in z and not z.startswith("Etc/"))
    extra = [current] if current and current not in common and current not in rest else []
    return [("US & common", [(z, z.replace("_", " ")) for z in common]), ("All", [(z, z) for z in extra + rest])]


class NewEventForm(forms.Form):
    """Create an event, optionally copying chosen parts of an existing one
    (`events.copying.PARTS`). Organizations, series and source events are
    limited to orgs where the user is staff."""

    organization = forms.ModelChoiceField(queryset=None)
    copy_from = forms.ModelChoiceField(
        queryset=None, required=False, label="Copy from an existing event", empty_label="Nothing: start from scratch"
    )
    name = forms.CharField(max_length=255, help_text='e.g. "NICAR 2027"')
    short_name = forms.CharField(max_length=64, required=False, help_text='Hashtag-style, e.g. "NICAR27"')
    slug = forms.SlugField(help_text="Used in URLs and published file names. Don't change it after publishing.")
    series = forms.ModelChoiceField(queryset=None, required=False, empty_label="None")
    new_series = forms.CharField(max_length=255, required=False, label="…or start a new series")
    kind = forms.ChoiceField(choices=())
    format = forms.ChoiceField(choices=())
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    timezone = forms.ChoiceField(choices=())
    parts = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple, label="Copy these parts")

    def __init__(self, *args, org_ids, **kwargs):
        from apps.events.copying import PARTS
        from apps.events.models import Event, EventSeries
        from apps.organizations.models import Organization

        super().__init__(*args, **kwargs)
        self.fields["organization"].queryset = Organization.objects.filter(pk__in=org_ids).order_by("name")
        self.fields["copy_from"].queryset = (
            Event.objects.filter(organization_id__in=org_ids).select_related("organization").order_by("-start_date")
        )
        self.fields["series"].queryset = EventSeries.objects.filter(organization_id__in=org_ids).order_by("name")
        self.fields["kind"].choices = Event.Kind.choices
        self.fields["format"].choices = Event.Format.choices
        self.fields["timezone"].choices = _timezone_choices(self.initial.get("timezone", ""))
        self.fields["parts"].choices = [(k, label) for k, label, _ in PARTS]
        self.part_help = {k: help_text for k, _, help_text in PARTS}
        if len(org_ids) == 1:
            self.fields["organization"].widget = forms.HiddenInput()
            self.fields["organization"].initial = org_ids[0]
        for name, field in self.fields.items():
            if name != "parts" and not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs.setdefault("class", "input")

    def clean(self):
        from apps.events.models import Event

        data = super().clean()
        org = data.get("organization")
        if org is None:
            return data
        for key in ("copy_from", "series"):
            if data.get(key) and data[key].organization_id != org.pk:
                self.add_error(key, "Pick one from the same organization.")
        if data.get("series") and data.get("new_series"):
            self.add_error("new_series", "Choose an existing series or name a new one, not both.")
        if data.get("slug") and Event.objects.filter(organization=org, slug=data["slug"]).exists():
            self.add_error("slug", "Another event in this organization already uses that slug.")
        start, end = data.get("start_date"), data.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "The event can't end before it starts.")
        if data.get("parts") and not data.get("copy_from"):
            data["parts"] = []
        return data
