"""Pitch workflow: submit → queue decision → inform → accept into a session.

All state changes to PitchTarget go through here (admin actions and API
call these) so the lifecycle rules live in one place.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.program.models import Session, SessionSpeaker, SessionType, SkillLevel
from apps.speakers import sharing
from apps.speakers.models import EventSpeaker, SpeakerShareGrant

from .models import FormQuestion, Pitch, PitchSpeaker, PitchTarget

Status = PitchTarget.Status


class PitchWorkflowError(ValidationError):
    pass


# ------------------------------------------------------------------ submit


def validate_answers(event, answers: dict, form: str = FormQuestion.Form.PITCH) -> dict:
    """Check `answers` against the event's custom questions; returns cleaned answers."""
    cleaned, errors = {}, {}
    for q in FormQuestion.objects.filter(event=event, form=form):
        value = answers.get(q.slug)
        if value in (None, "", []):
            if q.required:
                errors[q.slug] = "This question is required."
            continue
        kind = FormQuestion.Kind
        if q.kind == kind.INTEGER:
            try:
                value = int(value)
            except (TypeError, ValueError):
                errors[q.slug] = "Enter a whole number."
                continue
        elif q.kind == kind.BOOLEAN:
            value = value in (True, "true", "True", "yes", "1", 1)
        elif q.kind == kind.SINGLE_CHOICE and value not in q.choices:
            errors[q.slug] = f"Choose one of: {', '.join(q.choices)}."
            continue
        elif q.kind == kind.MULTI_CHOICE:
            value = list(value) if isinstance(value, list | tuple) else [value]
            if not set(value) <= set(q.choices):
                errors[q.slug] = f"Choose from: {', '.join(q.choices)}."
                continue
        cleaned[q.slug] = value
    if errors:
        raise ValidationError(errors)
    return cleaned


def submit_pitch(
    *,
    submitter,
    title: str,
    description: str,
    events: Iterable,
    co_speakers: Iterable = (),
    answers_by_event: dict | None = None,
    user=None,
    enforce_window: bool = True,
    **pitch_fields,
) -> Pitch:
    """Create a pitch and one target per event.

    Pitching grants each event's organization access to the submitter's
    private profile fields (speaker-controlled sharing; see speakers.sharing).
    Staff entering pitches on someone's behalf pass `enforce_window=False`.
    """
    events = list(events)
    if not events:
        raise PitchWorkflowError("Choose at least one event.")
    if enforce_window:
        closed = [e.name for e in events if not e.is_pitch_window_open()]
        if closed:
            raise PitchWorkflowError(f"Pitches aren't open for: {', '.join(closed)}.")
    answers_by_event = answers_by_event or {}
    cleaned = {e.pk: validate_answers(e, answers_by_event.get(e.pk, {})) for e in events}

    with transaction.atomic():
        pitch = Pitch.objects.create(
            submitted_by=submitter,
            submitted_by_user=user,
            title=title,
            description=description,
            **pitch_fields,
        )
        PitchSpeaker.objects.create(pitch=pitch, speaker=submitter, sort_order=0)
        for i, sp in enumerate(co_speakers, start=1):
            if sp.pk != submitter.pk:
                PitchSpeaker.objects.get_or_create(pitch=pitch, speaker=sp, defaults={"sort_order": i})
        for event in events:
            PitchTarget.objects.create(pitch=pitch, event=event, answers=cleaned[event.pk])
            sharing.grant(submitter, event.organization, SpeakerShareGrant.Source.PITCH)
    return pitch


# ------------------------------------------------------------------ review


def queue_decision(targets: Iterable[PitchTarget], decision: str, user) -> int:
    """Stage a decision without notifying anyone yet."""
    if decision not in (Status.ACCEPT_QUEUE, Status.DECLINE_QUEUE, Status.WAITLISTED, Status.SUBMITTED):
        raise PitchWorkflowError(f"Can't queue {decision!r}.")
    count = 0
    for target in targets:
        if target.status in PitchTarget.FINAL:
            continue
        target.status = decision
        target.decided_by = user
        target.decided_at = timezone.now()
        target.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
        count += 1
    return count


def inform(targets: Iterable[PitchTarget], *, notifier=None) -> dict[str, int]:
    """Finalize queued decisions and notify pitchers in one go.

    ACCEPT_QUEUE → ACCEPTED (and a Session is created via `accept`),
    DECLINE_QUEUE → DECLINED. `notifier(target)` sends the message; the
    default does nothing yet (email templates are phase 2).
    """
    counts = {"accepted": 0, "declined": 0, "skipped": 0}
    for target in targets:
        if target.status == Status.ACCEPT_QUEUE:
            accept(target, user=target.decided_by)
            counts["accepted"] += 1
        elif target.status == Status.DECLINE_QUEUE:
            target.status = Status.DECLINED
            target.save(update_fields=["status", "updated_at"])
            counts["declined"] += 1
        else:
            counts["skipped"] += 1
            continue
        if notifier:
            notifier(target)
        target.informed_at = timezone.now()
        target.save(update_fields=["informed_at", "updated_at"])
    return counts


def confirm(target: PitchTarget) -> None:
    """The pitcher confirms they'll do the accepted session."""
    if target.status != Status.ACCEPTED:
        raise PitchWorkflowError("Only accepted pitches can be confirmed.")
    target.confirmed_at = timezone.now()
    target.save(update_fields=["confirmed_at", "updated_at"])


@transaction.atomic
def accept(target: PitchTarget, user=None) -> Session:
    """Turn a target into a Session in the event's pool (unscheduled).

    Idempotent: returns the existing session if already accepted. Speakers
    from merged targets join the session; each speaker gets an EventSpeaker
    engagement (status INVITED) if they don't have one.
    """
    if target.session_id:
        return target.session
    if target.status in (Status.DECLINED, Status.MERGED, Status.WITHDRAWN):
        raise PitchWorkflowError(f"Can't accept a {target.get_status_display().lower()} pitch.")

    pitch, event = target.pitch, target.event
    org = event.organization
    session = Session.objects.create(
        event=event,
        title=pitch.title,
        title_status=Session.EditStatus.NEEDS_EDIT,
        description=pitch.description,
        description_status=Session.EditStatus.NEEDS_EDIT,
        source=Session.Source.PITCH,
        status=Session.Status.PLANNING,
        session_type=SessionType.objects.filter(organization=org, name__iexact=pitch.format).first()
        if pitch.format
        else None,
        skill_level=SkillLevel.objects.filter(organization=org, name__iexact=pitch.skill_level).first()
        if pitch.skill_level
        else None,
        owner=target.assigned_to,
        extra={"pitch_answers": target.answers} if target.answers else {},
    )
    session.tracks.set(target.suggested_tracks.all())

    speakers = list(pitch.pitch_speakers.all())
    for merged in target.merged_targets.select_related("pitch"):
        speakers += list(merged.pitch.pitch_speakers.all())
    seen = set()
    for order, ps in enumerate(speakers):
        if ps.speaker_id in seen:
            continue
        seen.add(ps.speaker_id)
        role = ps.role if ps.role in SessionSpeaker.Role.values else SessionSpeaker.Role.SPEAKER
        SessionSpeaker.objects.create(session=session, speaker_id=ps.speaker_id, role=role, sort_order=order)
        EventSpeaker.objects.get_or_create(event=event, speaker_id=ps.speaker_id)

    target.session = session
    target.status = Status.ACCEPTED
    target.decided_by = target.decided_by or user
    target.decided_at = target.decided_at or timezone.now()
    target.save(update_fields=["session", "status", "decided_by", "decided_at", "updated_at"])
    return session


@transaction.atomic
def merge(primary: PitchTarget, others: Iterable[PitchTarget], user=None) -> int:
    """Fold `others` into `primary` (same event). Their speakers join the
    primary's session when it's accepted (or now, if already accepted)."""
    count = 0
    for other in others:
        if other.pk == primary.pk:
            continue
        if other.event_id != primary.event_id:
            raise PitchWorkflowError("Can only merge pitches for the same event.")
        if other.session_id:
            raise PitchWorkflowError(f'"{other.pitch}" already has a session; merge sessions by hand.')
        other.merged_into = primary
        other.status = Status.MERGED
        other.decided_by = user
        other.decided_at = timezone.now()
        other.save(update_fields=["merged_into", "status", "decided_by", "decided_at", "updated_at"])
        count += 1
        if primary.session_id:
            existing = set(primary.session.session_speakers.values_list("speaker_id", flat=True))
            for ps in other.pitch.pitch_speakers.all():
                if ps.speaker_id not in existing:
                    SessionSpeaker.objects.create(session=primary.session, speaker_id=ps.speaker_id)
                    EventSpeaker.objects.get_or_create(event=primary.event, speaker_id=ps.speaker_id)
    return count
