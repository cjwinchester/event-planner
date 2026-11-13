"""Forms for the planner's side panels. Times are entered as event-local
wall-clock values and converted with the event's timezone."""

from __future__ import annotations

from datetime import datetime

from django import forms

from apps.events.models import EventRoom
from apps.program.models import Session, SessionType, SkillLevel, TimeSlot, Track


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
