"""Zoom connector (HTTP faked) and meeting sync (connector faked)."""

from datetime import UTC, datetime

import pytest
from django.core.cache import cache

from apps.events.models import EventRoom, EventVenue
from apps.integrations import meetings
from apps.integrations.base import AttendanceRecord, ConnectorContext, MeetingInfo
from apps.integrations.connectors.zoom import ZoomConnector
from apps.integrations.connectors.zoom import client as zoom_client
from apps.integrations.models import IntegrationConfig, SyncRun
from apps.program.models import Session, VirtualMeeting
from apps.venues.models import Room, Venue
from conftest import at

CREDS = {"account_id": "acct", "client_id": "cid", "client_secret": "secret"}


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data
        self.ok = 200 <= status < 300
        self.content = b"" if data is None else b"x"
        self.text = ""

    def json(self):
        return self._data or {}


class FakeZoomHTTP:
    """Records calls; serves canned responses keyed by (method, path)."""

    def __init__(self, responses):
        self.responses, self.calls, self.tokens = responses, [], 0

    def post(self, url, params=None, auth=None, timeout=None):
        assert url == zoom_client.TOKEN_URL and params["account_id"] == "acct" and auth == ("cid", "secret")
        self.tokens += 1
        return FakeResponse(200, {"access_token": f"tok{self.tokens}", "expires_in": 3600})

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        path = url.removeprefix(zoom_client.API)
        self.calls.append((method, path, kwargs.get("json"), kwargs.get("params"), headers["Authorization"]))
        queue = self.responses[(method, path)]
        return queue.pop(0) if isinstance(queue, list) else queue


@pytest.fixture
def zoom_http(monkeypatch):
    cache.clear()

    def install(responses):
        fake = FakeZoomHTTP(responses)
        monkeypatch.setattr(zoom_client.requests, "post", fake.post)
        monkeypatch.setattr(zoom_client.requests, "request", fake.request)
        return fake

    yield install
    cache.clear()


class TestZoomConnector:
    def ctx(self, **options):
        return ConnectorContext(organization=None, credentials=CREDS, options=options)

    def test_create_meeting_in_host_account(self, zoom_http):
        fake = zoom_http(
            {
                ("POST", "/users/room1@ire.example/meetings"): FakeResponse(
                    201, {"id": 123, "join_url": "https://z/j/123"}
                )
            }
        )
        info = ZoomConnector().create_meeting(
            self.ctx(meeting_settings={"waiting_room": True}),
            host="room1@ire.example",
            kind="meeting",
            title="Filing better FOIAs",
            starts_at=datetime(2026, 10, 8, 15, 0, tzinfo=UTC),
            minutes=60,
            recorded=True,
        )
        assert info.external_id == "123" and info.join_url == "https://z/j/123"
        _, _, body, _, auth = fake.calls[0]
        assert body["type"] == 2 and body["start_time"] == "2026-10-08T15:00:00Z" and body["duration"] == 60
        assert body["settings"] == {"auto_recording": "cloud", "waiting_room": True}
        assert auth == "Bearer tok1"

    def test_webinar_paths_reauth_on_401_and_missing_delete(self, zoom_http):
        fake = zoom_http(
            {
                ("PATCH", "/webinars/9"): [FakeResponse(401, {"message": "expired"}), FakeResponse(204)],
                ("DELETE", "/webinars/9"): FakeResponse(404, {"message": "gone"}),
            }
        )
        c = ZoomConnector()
        c.update_meeting(
            self.ctx(), external_id="9", kind="webinar", title="Keynote",
            starts_at=datetime(2026, 10, 8, 18, 30, tzinfo=UTC), minutes=60, recorded=False,
        )  # fmt: skip
        assert [call[4] for call in fake.calls] == ["Bearer tok1", "Bearer tok2"]
        assert fake.calls[1][2]["type"] == 5
        c.delete_meeting(self.ctx(), external_id="9", kind="webinar")  # 404 is fine

    def test_attendance_pages(self, zoom_http):
        path = "/report/meetings/123/participants"
        zoom_http(
            {
                ("GET", path): [
                    FakeResponse(
                        200, {"participants": [{"participant_user_id": "a", "duration": 600}], "next_page_token": "n"}
                    ),
                    FakeResponse(200, {"participants": [{"user_email": "b@x.org", "duration": 60}]}),
                ]
            }
        )
        rows = list(ZoomConnector().fetch_attendance(self.ctx(), external_id="123", kind="meeting"))
        assert [(r.participant_id, r.email, r.minutes) for r in rows] == [("a", "", 10), ("", "b@x.org", 1)]

    def test_check_host(self, zoom_http):
        zoom_http(
            {
                ("GET", "/users/r1@x"): FakeResponse(200, {"type": 2, "status": "active"}),
                ("GET", "/users/basic@x"): FakeResponse(200, {"type": 1, "status": "active"}),
                ("GET", "/users/web@x"): FakeResponse(200, {"type": 2, "status": "active"}),
                ("GET", "/users/web@x/settings"): FakeResponse(200, {"feature": {"webinar": False}}),
                ("GET", "/users/ghost@x"): FakeResponse(404, {"message": "User does not exist"}),
            }
        )
        c, ctx = ZoomConnector(), self.ctx()
        assert c.check_host(ctx, host="r1@x", kind="meeting") == (True, "Licensed.")
        assert c.check_host(ctx, host="basic@x", kind="meeting")[0] is False
        assert "no webinar license" in c.check_host(ctx, host="web@x", kind="webinar")[1]
        assert "Not found" in c.check_host(ctx, host="ghost@x", kind="meeting")[1]

    def test_errors_carry_zoom_message(self, zoom_http):
        from apps.integrations.base import ConnectorError

        zoom_http({("POST", "/users/nobody@x/meetings"): FakeResponse(404, {"message": "User does not exist"})})
        with pytest.raises(ConnectorError, match="User does not exist"):
            ZoomConnector().create_meeting(
                self.ctx(), host="nobody@x", kind="meeting", title="T",
                starts_at=datetime(2026, 10, 8, tzinfo=UTC), minutes=30, recorded=False,
            )  # fmt: skip


class FakeZoom:
    slug = "zoom"

    def __init__(self):
        self.log, self.n = [], 0

    def create_meeting(self, ctx, *, host, kind, title, starts_at, minutes, recorded):
        self.n += 1
        self.log.append(("create", host, kind, title))
        return MeetingInfo(external_id=str(self.n), join_url=f"https://z/j/{self.n}")

    def update_meeting(self, ctx, *, external_id, kind, title, starts_at, minutes, recorded):
        self.log.append(("update", external_id, minutes))

    def delete_meeting(self, ctx, *, external_id, kind):
        self.log.append(("delete", external_id))

    def test_connection(self, ctx):
        from apps.integrations.base import ConnectionStatus

        return ConnectionStatus(True, "Connected as owner@ire.example.")

    def check_host(self, ctx, *, host, kind):
        return (host != "r2@ire.example", "Licensed." if host != "r2@ire.example" else "Basic (unlicensed).")

    def fetch_attendance(self, ctx, *, external_id, kind):
        yield AttendanceRecord(participant_id="a")
        yield AttendanceRecord(participant_id="a")  # rejoin
        yield AttendanceRecord(email="B@x.org")


@pytest.fixture
def zoomed(world, make_session, monkeypatch):
    fake = FakeZoom()
    monkeypatch.setattr(meetings, "enabled_connector", lambda org, cap, event=None, prefer="": (fake, None))
    world.event.virtual_meetings = True
    world.event.save()
    venue = Venue.objects.create(name="IRE Zoom", kind=Venue.Kind.VIRTUAL, owner=world.org)
    r1 = Room.objects.create(venue=venue, name="Zoom Room 1", host_account="r1@ire.example")
    r2 = Room.objects.create(venue=venue, name="Zoom Room 2", host_account="r2@ire.example")
    EventVenue.objects.create(event=world.event, venue=venue)
    er1 = EventRoom.objects.create(event=world.event, room=r1)
    er2 = EventRoom.objects.create(event=world.event, room=r2)
    s = make_session("Session", room=er1, start=at(5, 11), end=at(5, 12))
    make_session("In the hotel room: no Zoom host", room=world.er_a, start=at(5, 11), end=at(5, 12))
    return world, fake, s, er1, er2


@pytest.mark.django_db
class TestMeetingSync:
    def test_lifecycle(self, zoomed):
        world, fake, s, _er1, er2 = zoomed
        assert meetings.meeting_plan(world.event)["create"] == 1
        run = meetings.sync_meetings(world.event)
        assert run.status == SyncRun.Status.SUCCEEDED and run.stats["created"] == 1
        assert fake.log == [("create", "r1@ire.example", "meeting", "Session")]
        assert VirtualMeeting.objects.get(session=s).join_url == "https://z/j/1"

        assert meetings.sync_meetings(world.event).stats["updated"] == 0  # nothing changed: no calls
        assert len(fake.log) == 1

        s.ends_at = at(5, 12, 30)
        s.save()
        meetings.sync_meetings(world.event)
        assert fake.log[-1] == ("update", "1", 90)

        s.room = er2  # another Zoom user: new meeting
        s.save()
        assert meetings.sync_meetings(world.event).stats["recreated"] == 1
        assert fake.log[-2:] == [("delete", "1"), ("create", "r2@ire.example", "meeting", "Session")]

        s.status = Session.Status.CANCELED
        s.save()
        assert meetings.sync_meetings(world.event).stats["deleted"] == 1
        assert not VirtualMeeting.objects.exists()

    def test_attendance_counts_only_ended(self, zoomed):
        world, _fake, s, *_ = zoomed
        meetings.sync_meetings(world.event)
        assert meetings.pull_attendance(world.event).stats["pulled"] == 0  # 2027: not over yet
        Session.objects.filter(pk=s.pk).update(
            starts_at=at(5, 11).replace(year=2020), ends_at=at(5, 12).replace(year=2020)
        )
        assert meetings.pull_attendance(world.event).stats["pulled"] == 1
        m = VirtualMeeting.objects.get()
        assert (m.participant_count, m.unique_participant_count) == (3, 2)

    def test_off_for_event_never_calls_zoom(self, zoomed):
        world, fake, *_ = zoomed
        world.event.virtual_meetings = False
        world.event.save()
        run = meetings.sync_meetings(world.event)
        assert run.status == SyncRun.Status.FAILED and "turned off" in run.message and fake.log == []

    def test_ended_sessions_are_left_alone(self, zoomed):
        world, fake, s, *_ = zoomed
        Session.objects.filter(pk=s.pk).update(
            starts_at=at(5, 11).replace(year=2020), ends_at=at(5, 12).replace(year=2020)
        )
        assert meetings.meeting_plan(world.event)["create"] == 0
        meetings.sync_meetings(world.event)
        assert fake.log == []

    def test_setup_checks(self, zoomed):
        world, fake, *_ = zoomed
        checks = {c.label: c for c in meetings.test_setup(world.event, write_test=True)}
        assert checks["Credentials"].ok
        assert checks["Zoom Room 1 (r1@ire.example)"].ok
        assert checks["Zoom Room 2 (r2@ire.example)"].ok is False
        assert checks["Test meeting"].ok and fake.log[-2:] == [
            ("create", "r1@ire.example", "meeting", "Event planner connection test (deleted)"),
            ("delete", "1"),
        ]
        assert not VirtualMeeting.objects.exists()  # the test meeting is never stored

    def test_not_connected(self, world, monkeypatch):
        monkeypatch.setattr(meetings, "enabled_connector", lambda *a, **k: (None, None))
        world.event.virtual_meetings = True
        run = meetings.sync_meetings(world.event)
        assert run.status == SyncRun.Status.FAILED and "isn't connected" in run.message


@pytest.mark.django_db
def test_run_of_show_queues_sync(client, world, monkeypatch):
    from django.urls import reverse

    IntegrationConfig.objects.create(organization=world.org, connector="zoom", credentials=CREDS)
    called = []
    monkeypatch.setattr(meetings, "sync_meetings", lambda event, triggered_by=None: called.append(event))
    client.force_login(world.staff_user)
    page_url = reverse("planner:run-of-show", args=[world.org.slug, world.event.slug])
    action_url = reverse("planner:zoom-action", args=[world.org.slug, world.event.slug])
    assert "Turn on Zoom meetings" in client.get(page_url).content.decode()
    client.post(action_url, {"action": "sync"})
    assert called == []  # off by default: nothing queued
    client.post(action_url, {"action": "on"})
    world.event.refresh_from_db()
    assert world.event.virtual_meetings
    assert "Create / update meetings" in client.get(page_url).content.decode()
    client.post(action_url, {"action": "sync"})
    assert called == [world.event]  # ImmediateBackend runs the task inline in tests


@pytest.mark.django_db
def test_admin_save_and_test_button(client, world, monkeypatch):
    from django.urls import reverse

    from apps.integrations import admin as integrations_admin
    from apps.integrations.base import ConnectionStatus

    config = IntegrationConfig.objects.create(organization=world.org, connector="zoom", credentials=CREDS)
    url = reverse("admin:integrations_integrationconfig_change", args=[config.pk])
    client.force_login(world.admin_user)
    assert "Save and test connection" in client.get(url).content.decode()

    def fake_test(cfg):
        cfg.last_test_ok, cfg.last_test_message = True, "Connected as owner@ire.example."
        cfg.save()
        return ConnectionStatus(True, cfg.last_test_message)

    monkeypatch.setattr(integrations_admin, "test_integration", fake_test)
    data = {"organization": world.org.pk, "connector": "zoom", "is_enabled": "on", "options": "{}", "_test": "1"}
    resp = client.post(url, data, follow=True)
    assert "Test passed: Connected as owner@ire.example." in resp.content.decode()
    config.refresh_from_db()
    assert config.credentials == CREDS  # blank credentials field keeps the stored ones


@pytest.mark.django_db(transaction=True)
def test_concurrent_sync_is_refused(zoomed):
    """A second sync while one holds the event's advisory lock does nothing (no duplicate meetings)."""
    from django.db import connections

    world, fake, *_ = zoomed
    other = connections.create_connection("default")  # a second session, like another worker
    try:
        with other.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s, %s)", [meetings.LOCK_NAMESPACE, world.event.pk])
        run = meetings.sync_meetings(world.event)
        assert run.status == SyncRun.Status.FAILED and "already running" in run.message and fake.log == []
        with other.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s, %s)", [meetings.LOCK_NAMESPACE, world.event.pk])
        assert meetings.sync_meetings(world.event).stats["created"] == 1
    finally:
        other.close()


def test_host_is_one_path_segment(zoom_http):
    fake = zoom_http({("GET", "/users/a%2F..%2Fadmin%3Fx%3D1"): FakeResponse(404, {"message": "nope"})})
    ok, _ = ZoomConnector().check_host(TestZoomConnector().ctx(), host="a/../admin?x=1", kind="meeting")
    assert not ok and fake.calls[0][1] == "/users/a%2F..%2Fadmin%3Fx%3D1"
