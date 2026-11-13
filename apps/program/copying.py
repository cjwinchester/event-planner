"""Event copy utility: carry stock/recurring sessions into another event.

    copy_sessions(source_event, target_event, sessions=None, ...)

By default copies the source's *stock* sessions (`is_stock=True`). Tracks
are matched by slug in the target (created if missing), session type and
skill level are org lookups so they carry over as-is within an org.
Speakers are optional (recurring sessions often change speakers). With
`keep_schedule=True` the copy keeps its weekday/time offset relative to the
event start and the same room *if* the target event uses that venue room.

Copies get a fresh public_id, `source=STOCK`, `copied_from=` the original,
status PLANNING and title/description status reset to PENDING so they go
back through editing before publishing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction

from .models import Session, SessionSpeaker, Track


@dataclass
class CopyResult:
    created: list[Session] = field(default_factory=list)
    skipped: list[tuple[Session, str]] = field(default_factory=list)
    tracks_created: list[Track] = field(default_factory=list)


def copy_sessions(
    source_event,
    target_event,
    sessions=None,
    *,
    include_speakers: bool = False,
    keep_schedule: bool = False,
    skip_existing_titles: bool = True,
) -> CopyResult:
    if source_event.organization_id != target_event.organization_id:
        # Cross-org copying would leak private lookups; share via stock
        # session visibility in a later phase instead.
        raise ValueError("Sessions can only be copied between events of the same organization.")
    if sessions is None:
        sessions = source_event.sessions.stock()
    sessions = list(sessions.prefetch_related("tracks", "session_speakers") if hasattr(sessions, "all") else sessions)

    result = CopyResult()
    existing_titles = {t.lower() for t in target_event.sessions.values_list("title", flat=True)}
    target_tracks = {t.slug: t for t in target_event.tracks.all()}
    target_rooms = {er.room_id: er for er in target_event.event_rooms.all()}
    day_offset = target_event.start_date - source_event.start_date

    with transaction.atomic():
        for original in sessions:
            if original.event_id != source_event.pk:
                result.skipped.append((original, "belongs to a different event"))
                continue
            if skip_existing_titles and original.title.lower() in existing_titles:
                result.skipped.append((original, "a session with this title already exists"))
                continue

            copy = Session(
                event=target_event,
                title=original.title,
                title_status=Session.EditStatus.PENDING,
                description=original.description,
                description_status=Session.EditStatus.PENDING,
                summary=original.summary,
                session_type=original.session_type,
                skill_level=original.skill_level,
                status=Session.Status.PLANNING,
                source=Session.Source.STOCK,
                is_stock=original.is_stock,
                is_plenary=original.is_plenary,
                copied_from=original,
                requires_preregistration=original.requires_preregistration,
                capacity=original.capacity,
                cost=original.cost,
                is_recorded=original.is_recorded,
                setup=original.setup,
                expected_headcount=original.expected_headcount,
                av_requirements=original.av_requirements,
                fnb_requirements=original.fnb_requirements,
                hotel_notes=original.hotel_notes,
                extra=dict(original.extra or {}),
            )
            if keep_schedule and original.starts_at and original.room_id:
                target_room = target_rooms.get(original.room.room_id)
                if target_room is not None:
                    copy.room = target_room
                    copy.starts_at = _shift(original.starts_at, day_offset, source_event, target_event)
                    copy.ends_at = _shift(original.ends_at, day_offset, source_event, target_event)
            copy.save()

            track_ids = []
            for track in original.tracks.all():
                target_track = target_tracks.get(track.slug)
                if target_track is None:
                    target_track = Track.objects.create(
                        event=target_event,
                        name=track.name,
                        slug=track.slug,
                        description=track.description,
                        color=track.color,
                        sort_order=track.sort_order,
                    )
                    target_tracks[track.slug] = target_track
                    result.tracks_created.append(target_track)
                track_ids.append(target_track.pk)
            copy.tracks.set(track_ids)

            if include_speakers:
                SessionSpeaker.objects.bulk_create(
                    SessionSpeaker(session=copy, speaker_id=ss.speaker_id, role=ss.role, sort_order=ss.sort_order)
                    for ss in original.session_speakers.all()
                )
            existing_titles.add(copy.title.lower())
            result.created.append(copy)
    return result


def _shift(dt, day_offset, source_event, target_event):
    """Same local wall-clock time, `day_offset` days later, in the target tz."""
    local = dt.astimezone(source_event.tz)
    shifted = local.replace(tzinfo=None) + day_offset
    return shifted.replace(tzinfo=target_event.tz)
