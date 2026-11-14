"""Keep each virtual session's platform meeting in step with the schedule.

`sync_meetings(event)` walks the event's sessions and, per session:

  needs a meeting, has none          → create (in the room's host account)
  has one, host or type changed      → delete + create (new join link)
  has one, time/title/recording moved→ update in place (same join link)
  has one, no longer needs it        → delete (canceled, unscheduled, moved
                                       to a room with no Zoom host)

Attendees never see the link directly (attendee passes redirect to the
current one), so a recreated meeting needs no re-send. One session failing
doesn't stop the rest; its error is kept on the meeting / in the SyncRun.

`pull_attendance(event)` stores per-session counts after sessions end:
joins, and unique participants. Participant names/emails are not stored.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.program.models import Session, VirtualMeeting, meeting_spec

from .base import Capability, ConnectorError
from .models import SyncRun
from .services import enabled_connector

logger = logging.getLogger(__name__)


def _run(event, operation, user) -> SyncRun:
    return SyncRun.objects.create(
        organization=event.organization,
        event=event,
        connector="zoom",
        operation=operation,
        status=SyncRun.Status.RUNNING,
        started_at=timezone.now(),
        triggered_by=user,
    )


def _finish(run: SyncRun, stats: dict, errors: list[str]) -> SyncRun:
    run.stats = stats
    run.status = SyncRun.Status.FAILED if errors and not any(stats.values()) else SyncRun.Status.SUCCEEDED
    run.message = "\n".join(errors[:50])
    run.finished_at = timezone.now()
    run.save()
    return run


def meeting_plan(event) -> dict:
    """Counts of what a sync would do (for the run-of-show page)."""
    plan = {"create": 0, "update": 0, "delete": 0, "ok": 0}
    for s in Session.objects.filter(event=event).select_related("room__room", "virtual_meeting"):
        meeting = getattr(s, "virtual_meeting", None)
        spec = meeting_spec(s)
        if meeting is None:
            plan["create"] += spec is not None
        elif spec is None:
            plan["delete"] += 1
        elif meeting.needs_sync():
            plan["update"] += 1
        else:
            plan["ok"] += 1
    return plan


def sync_meetings(event, *, triggered_by=None) -> SyncRun:
    run = _run(event, "sync_meetings", triggered_by)
    stats = {"created": 0, "updated": 0, "recreated": 0, "deleted": 0, "failed": 0}
    errors: list[str] = []
    connector, ctx = enabled_connector(event.organization, Capability.VIRTUAL_SESSIONS, event, prefer="zoom")
    if connector is None:
        return _finish(run, stats, ["Zoom isn't connected for this organization (Admin → Integration configs)."])
    run.connector = connector.slug
    sessions = Session.objects.filter(event=event).select_related("room__room", "virtual_meeting")
    for s in sessions:
        meeting = getattr(s, "virtual_meeting", None)
        spec = meeting_spec(s)
        if meeting is None and spec is None:
            continue
        try:
            if spec is None:
                connector.delete_meeting(ctx, external_id=meeting.external_id, kind=meeting.meeting_type)
                meeting.delete()
                stats["deleted"] += 1
            elif meeting is None:
                _create(connector, ctx, s, spec)
                stats["created"] += 1
            elif (meeting.host_account, meeting.meeting_type) != (spec["host_account"], spec["meeting_type"]):
                connector.delete_meeting(ctx, external_id=meeting.external_id, kind=meeting.meeting_type)
                meeting.delete()
                _create(connector, ctx, s, spec)
                stats["recreated"] += 1
            elif meeting.needs_sync():
                connector.update_meeting(
                    ctx,
                    external_id=meeting.external_id,
                    kind=spec["meeting_type"],
                    title=spec["topic"],
                    starts_at=spec["starts_at"],
                    minutes=spec["minutes"],
                    recorded=spec["recorded"],
                )
                _snapshot(meeting, spec)
                meeting.save()
                stats["updated"] += 1
        except ConnectorError as exc:
            stats["failed"] += 1
            errors.append(f"{s.title}: {exc}")
            if meeting is not None and meeting.pk:
                meeting.last_error = str(exc)
                meeting.save(update_fields=["last_error", "updated_at"])
            logger.warning("Meeting sync failed for session %s: %s", s.pk, exc)
    return _finish(run, stats, errors)


def _snapshot(meeting: VirtualMeeting, spec: dict) -> None:
    meeting.host_account = spec["host_account"]
    meeting.meeting_type = spec["meeting_type"]
    meeting.topic = spec["topic"]
    meeting.starts_at = spec["starts_at"]
    meeting.minutes = spec["minutes"]
    meeting.recorded = spec["recorded"]
    meeting.synced_at = timezone.now()
    meeting.last_error = ""


def _create(connector, ctx, session: Session, spec: dict) -> VirtualMeeting:
    info = connector.create_meeting(
        ctx,
        host=spec["host_account"],
        kind=spec["meeting_type"],
        title=spec["topic"],
        starts_at=spec["starts_at"],
        minutes=spec["minutes"],
        recorded=spec["recorded"],
    )
    meeting = VirtualMeeting(session=session, connector=connector.slug, external_id=info.external_id)
    meeting.join_url = info.join_url
    _snapshot(meeting, spec)
    meeting.save()
    session.virtual_meeting = meeting
    return meeting


def pull_attendance(event, *, triggered_by=None, refresh: bool = False) -> SyncRun:
    """Counts for meetings whose session has ended (Zoom's reports appear
    shortly after a meeting ends). `refresh` re-pulls ones already counted."""
    run = _run(event, "pull_attendance", triggered_by)
    stats = {"pulled": 0, "failed": 0}
    errors: list[str] = []
    connector, ctx = enabled_connector(event.organization, Capability.VIRTUAL_SESSIONS, event, prefer="zoom")
    if connector is None:
        return _finish(run, stats, ["Zoom isn't connected for this organization."])
    meetings = VirtualMeeting.objects.filter(session__event=event, session__ends_at__lt=timezone.now())
    if not refresh:
        meetings = meetings.filter(attendance_pulled_at__isnull=True)
    for m in meetings.select_related("session"):
        try:
            rows = list(connector.fetch_attendance(ctx, external_id=m.external_id, kind=m.meeting_type))
        except ConnectorError as exc:
            stats["failed"] += 1
            errors.append(f"{m.session.title}: {exc}")
            continue
        people = {r.participant_id or r.email.lower() or r.name for r in rows}
        m.participant_count = len(rows)
        m.unique_participant_count = len(people - {""})
        m.attendance_pulled_at = timezone.now()
        m.save(update_fields=["participant_count", "unique_participant_count", "attendance_pulled_at", "updated_at"])
        stats["pulled"] += 1
    return _finish(run, stats, errors)
