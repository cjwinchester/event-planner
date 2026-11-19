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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

from django.db import connection
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


def _current(event):
    """Sessions a sync may touch: not yet over (past meetings are history)."""
    now = timezone.now()
    return Session.objects.filter(event=event).exclude(ends_at__lt=now).select_related("room__room", "virtual_meeting")


def meeting_plan(event) -> dict:
    """Counts of what a sync would do (for the run-of-show page)."""
    plan = {"create": 0, "update": 0, "delete": 0, "ok": 0}
    for s in _current(event):
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


LOCK_NAMESPACE = 7310  # pg advisory lock class for "meeting sync" (arbitrary, stable)


@contextmanager
def _event_lock(event):
    """Postgres advisory lock per event, held for the whole sync without a
    long transaction. Yields False if another sync holds it (a double click,
    or a queued run overlapping a manual one), which would otherwise create
    duplicate meetings in Zoom."""
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s, %s)", [LOCK_NAMESPACE, event.pk])
        got = cur.fetchone()[0]
    try:
        yield got
    finally:
        if got:
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s, %s)", [LOCK_NAMESPACE, event.pk])


def sync_meetings(event, *, triggered_by=None) -> SyncRun:
    with _event_lock(event) as locked:
        if not locked:
            run = _run(event, "sync_meetings", triggered_by)
            return _finish(run, {}, ["Another meeting sync for this event is already running; try again shortly."])
        return _sync_meetings(event, triggered_by)


def _sync_meetings(event, triggered_by) -> SyncRun:
    run = _run(event, "sync_meetings", triggered_by)
    stats = {"created": 0, "updated": 0, "recreated": 0, "deleted": 0, "failed": 0}
    errors: list[str] = []
    if not event.virtual_meetings:
        return _finish(run, stats, ["Zoom meetings are turned off for this event."])
    connector, ctx = enabled_connector(event.organization, Capability.VIRTUAL_SESSIONS, event, prefer="zoom")
    if connector is None:
        return _finish(run, stats, ["Zoom isn't connected for this organization (Admin → Integration configs)."])
    run.connector = connector.slug
    for s in _current(event):
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


@dataclass
class Check:
    label: str
    ok: bool | None  # None = warning / not applicable
    detail: str = ""


def test_setup(event, *, write_test: bool = False) -> list[Check]:
    """Check Zoom is ready for this event without touching real sessions:
    credentials, each virtual room's host (exists, licensed, webinar add-on
    for webinar rooms) and, if `write_test`, create + delete a throwaway
    meeting in the first host's account to prove the app's write scopes."""
    checks: list[Check] = []
    connector, ctx = enabled_connector(event.organization, Capability.VIRTUAL_SESSIONS, event, prefer="zoom")
    if connector is None:
        return [Check("Zoom connection", False, "No enabled Zoom integration config for this organization.")]
    status = connector.test_connection(ctx)
    checks.append(Check("Credentials", status.ok, status.message))
    if not status.ok:
        return checks
    rooms = [er.room for er in event.event_rooms.select_related("room__venue") if er.room.venue.is_virtual]
    if not rooms:
        checks.append(Check("Rooms", None, "This event has no virtual rooms yet (grid → Rooms)."))
    for room in rooms:
        if not room.host_account:
            checks.append(Check(room.name, None, "No Zoom host set, so its sessions won't get meetings."))
            continue
        ok, detail = connector.check_host(ctx, host=room.host_account, kind=room.meeting_type or "meeting")
        checks.append(Check(f"{room.name} ({room.host_account})", ok, detail))
    if write_test:
        host = next((r for r in rooms if r.host_account), None)
        if host is None:
            checks.append(Check("Test meeting", None, "Skipped: no room has a Zoom host."))
        else:
            kind = host.meeting_type or "meeting"
            try:
                info = connector.create_meeting(
                    ctx, host=host.host_account, kind=kind, title="Event planner connection test (deleted)",
                    starts_at=timezone.now() + timedelta(days=30), minutes=15, recorded=False,
                )  # fmt: skip
                connector.delete_meeting(ctx, external_id=info.external_id, kind=kind)
                checks.append(Check("Test meeting", True, f"Created and deleted a {kind} as {host.host_account}."))
            except ConnectorError as exc:
                checks.append(Check("Test meeting", False, str(exc)))
    return checks
