"""What a speaker can do to their own sessions (speaker portal), and the same
confirm action for staff acting on a speaker's behalf.

Rules (agreed with the project owner):
  * Speakers on a session may edit its details until the event's
    `speaker_edit_deadline`; after it (or if none is set) only staff can.
    Edits apply immediately and are logged as a `SpeakerUpdate` for staff to
    review, with before/after values.
  * Only confirmed speakers are published; a speaker confirms each session
    role (or staff mark it confirmed for them).
  * A speaker may always withdraw from a session. Their role is removed
    (so feeds drop them on the next publish) and staff see the withdrawal.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.speakers.models import EventSpeaker

from .models import SessionSpeaker, SpeakerUpdate

EDITABLE_FIELDS = ("title", "summary", "description")


class SpeakerActionError(ValueError):
    pass


def can_edit(event, now=None) -> bool:
    deadline = event.speaker_edit_deadline
    return bool(deadline and (now or timezone.now()) <= deadline)


def _log(ss: SessionSpeaker, kind: str, user, changes=None) -> SpeakerUpdate:
    return SpeakerUpdate.objects.create(
        event_id=ss.session.event_id,
        session=ss.session,
        session_title=ss.session.title,
        speaker=ss.speaker,
        user=user,
        kind=kind,
        changes=changes or {},
    )


def edit_session(ss: SessionSpeaker, user, data: dict) -> SpeakerUpdate | None:
    """Apply a speaker's edits to `ss.session`; returns the log entry (None if nothing changed)."""
    session = ss.session
    if not can_edit(session.event):
        raise SpeakerActionError("The deadline for speaker edits has passed. Contact the organizers to make changes.")
    if session.is_canceled:
        raise SpeakerActionError("This session was canceled.")
    changes = {}
    for f in EDITABLE_FIELDS:
        if f in data and (data[f] or "") != getattr(session, f):
            changes[f] = [getattr(session, f), data[f] or ""]
            setattr(session, f, data[f] or "")
    if not changes:
        return None
    with transaction.atomic():
        session.save(update_fields=[*changes, "updated_at"])
        return _log(ss, SpeakerUpdate.Kind.EDITED, user, changes)


def confirm(ss: SessionSpeaker, user, *, by_staff: bool = False) -> None:
    if ss.is_confirmed:
        return
    with transaction.atomic():
        ss.is_confirmed, ss.confirmed_at = True, timezone.now()
        ss.save(update_fields=["is_confirmed", "confirmed_at"])
        es, _ = EventSpeaker.objects.get_or_create(event_id=ss.session.event_id, speaker=ss.speaker)
        if es.status in (EventSpeaker.Status.PROSPECT, EventSpeaker.Status.INVITED):
            es.status = EventSpeaker.Status.CONFIRMED
            es.save(update_fields=["status", "updated_at"])
        if not by_staff:
            _log(ss, SpeakerUpdate.Kind.CONFIRMED, user)


def withdraw(ss: SessionSpeaker, user, *, reason: str = "") -> SpeakerUpdate:
    """Always allowed. Removes the role; if it was the speaker's last session at
    the event, their engagement is marked withdrawn too."""
    with transaction.atomic():
        entry = _log(ss, SpeakerUpdate.Kind.WITHDREW, user, {"reason": reason} if reason else {})
        event_id, speaker = ss.session.event_id, ss.speaker
        ss.delete()
        if not SessionSpeaker.objects.filter(session__event_id=event_id, speaker=speaker).exists():
            EventSpeaker.objects.filter(event_id=event_id, speaker=speaker).update(
                status=EventSpeaker.Status.WITHDRAWN, updated_at=timezone.now()
            )
    return entry


PROFILE_FIELDS = ("affiliation", "job_title", "bio")


def edit_event_profile(engagement, user, data: dict | None = None, *, from_profile: bool = False):
    """The speaker updates what this event's schedule shows about them (until the
    event's speaker-edit deadline): new values, or a fresh copy of their current
    shared profile. Logged for staff like session edits."""
    event = engagement.event
    if not can_edit(event):
        raise SpeakerActionError("The deadline for speaker edits has passed. Contact the organizers to make changes.")
    before = {f: getattr(engagement, f) for f in (*PROFILE_FIELDS, "headshot", "headshot_url")}
    if from_profile:
        engagement.copy_from_profile(overwrite=True)
    else:
        for f in PROFILE_FIELDS:
            if f in data:
                setattr(engagement, f, data[f] or "")
    changes = {
        f: [str(before[f] or ""), str(getattr(engagement, f) or "")]
        for f in before
        if str(before[f] or "") != str(getattr(engagement, f) or "")
    }
    if not changes:
        return None
    with transaction.atomic():
        engagement.save()
        return SpeakerUpdate.objects.create(
            event=event,
            session_title="Speaker details for this event",
            speaker=engagement.speaker,
            user=user,
            kind=SpeakerUpdate.Kind.EVENT_PROFILE,
            changes=changes,
        )
