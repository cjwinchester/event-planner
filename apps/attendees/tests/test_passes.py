import csv
import io

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.attendees.models import AttendeePass, JoinClick
from apps.attendees.services import issue_passes
from apps.attendees.timefmt import ap_range
from apps.events.models import EventRoom, EventVenue, Registration
from apps.program.models import Session, VirtualMeeting
from apps.speakers import sharing
from apps.speakers.models import SpeakerShareGrant
from apps.venues.models import Room, Venue
from conftest import at

JOIN = "https://zoom.example/j/111?pwd=abc"


@pytest.fixture
def af(world, make_session):
    """A published event with a Zoom room, a session with a meeting, a draft, and registrants."""
    venue = Venue.objects.create(name="IRE Zoom", kind=Venue.Kind.VIRTUAL, owner=world.org)
    room = Room.objects.create(venue=venue, name="Zoom Room 1", host_account="r1@ire.example")
    EventVenue.objects.create(event=world.event, venue=venue)
    er = EventRoom.objects.create(event=world.event, room=room)
    live = make_session("Live one", room=er, start=at(5, 11), end=at(5, 12), speakers=[world.alice])
    VirtualMeeting.objects.create(
        session=live, external_id="111", join_url=JOIN, host_account="r1@ire.example", starts_at=live.starts_at,
        minutes=60, topic="Live one",
    )  # fmt: skip
    draft = make_session("Draft", room=er, start=at(5, 13), end=at(5, 14), edited=False)
    now = timezone.now()
    Registration.objects.create(event=world.event, first_name="Pat", last_name="Reader", email="pat@example.com",
                                registered_at=now)  # fmt: skip
    Registration.objects.create(event=world.event, first_name="Alice", last_name="A", email="ALICE@example.com",
                                registered_at=now)  # fmt: skip
    Registration.objects.create(event=world.event, first_name="Gone", email="gone@example.com", registered_at=now,
                                canceled_at=now)  # fmt: skip
    return world, live, draft


@pytest.mark.django_db
class TestIssue:
    def test_speakers_then_registrants_deduped_by_email(self, af):
        world, *_ = af
        sharing.grant(world.alice, world.org, SpeakerShareGrant.Source.PITCH)
        result = issue_passes(world.event)
        passes = {p.name: p for p in AttendeePass.objects.filter(event=world.event)}
        assert set(passes) == {"Alice Analyst", "Pat Reader"}  # canceled registrant skipped
        alice = passes["Alice Analyst"]
        assert alice.kind == "speaker" and alice.registration.email == "ALICE@example.com"
        assert result.created == 2
        assert issue_passes(world.event).created == 0  # idempotent

    def test_speaker_email_needs_share_grant(self, af):
        world, *_ = af
        Registration.objects.filter(email__iexact="alice@example.com").delete()
        issue_passes(world.event)
        assert AttendeePass.objects.get(speaker=world.alice).email == ""


@pytest.mark.django_db
class TestPassPages:
    def test_page_join_redirect_and_click_log(self, client, af):
        world, live, _ = af
        issue_passes(world.event)
        p = AttendeePass.objects.get(name="Pat Reader")
        resp = client.get(reverse("attendees:home", args=[p.token]))
        body = resp.content.decode()
        assert resp["Cache-Control"] == "private, no-store" and resp["Referrer-Policy"] == "no-referrer"
        assert "Live one" in body and "Draft" not in body and "zoom.example" not in body  # link only via redirect
        assert "Sessions starting at 11 a.m." in body
        join = client.get(reverse("attendees:join", args=[p.token, live.public_id]))
        assert join.status_code == 302 and join["Location"] == JOIN
        assert JoinClick.objects.get().session == live
        p.refresh_from_db()
        assert p.use_count == 2 and p.first_used_at

    def test_drafts_and_canceled_sessions_have_no_link(self, client, af):
        world, live, draft = af
        issue_passes(world.event)
        p = AttendeePass.objects.get(name="Pat Reader")
        assert client.get(reverse("attendees:join", args=[p.token, draft.public_id])).status_code == 404
        live.status = Session.Status.CANCELED
        live.save()
        assert client.get(reverse("attendees:join", args=[p.token, live.public_id])).status_code == 404

    def test_revoke_cancel_and_reissue(self, client, af):
        world, live, _ = af
        issue_passes(world.event)
        p = AttendeePass.objects.get(name="Pat Reader")
        old = p.token
        p.reissue()
        assert client.get(reverse("attendees:home", args=[old])).status_code == 404
        assert client.get(reverse("attendees:home", args=[p.token])).status_code == 200
        p.registration.canceled_at = timezone.now()
        p.registration.save()
        assert client.get(reverse("attendees:join", args=[p.token, live.public_id])).status_code == 404

    def test_calendar_uses_personal_redirect(self, client, af):
        world, live, _ = af
        issue_passes(world.event)
        p = AttendeePass.objects.get(name="Pat Reader")
        ics = client.get(reverse("attendees:calendar", args=[p.token])).content.decode().replace("\r\n ", "")
        assert f"/my/{p.token}/join/{live.public_id}/" in ics and "zoom.example" not in ics

    def test_speaker_sees_own_session_marked(self, client, af):
        world, *_ = af
        issue_passes(world.event)
        p = AttendeePass.objects.get(speaker=world.alice)
        assert "Your session" in client.get(reverse("attendees:home", args=[p.token])).content.decode()


@pytest.mark.django_db
class TestStaffPages:
    def test_csv_and_permissions(self, client, af):
        world, *_ = af
        url = reverse("planner:passes-csv", args=[world.org.slug, world.event.slug])
        client.force_login(world.viewer_user)
        assert client.get(url).status_code == 403
        client.force_login(world.outsider)
        assert client.get(url).status_code == 403
        client.force_login(world.staff_user)
        client.post(reverse("planner:passes-issue", args=[world.org.slug, world.event.slug]))
        rows = list(csv.DictReader(io.StringIO(client.get(url).content.decode())))
        pat = next(r for r in rows if r["email"] == "pat@example.com")
        assert pat["first_name"] == "Pat" and pat["link"].startswith("http://testserver/my/")

    def test_run_of_show_shows_links_to_staff_only(self, client, af):
        world, *_ = af
        url = reverse("planner:run-of-show", args=[world.org.slug, world.event.slug])
        client.force_login(world.viewer_user)
        assert client.get(url).status_code == 403
        client.force_login(world.staff_user)
        body = client.get(url).content.decode()
        assert "zoom.example" in body and "r1@ire.example" in body and "Own meeting" in body
        assert "11:00 AM–12:00 PM" in body  # event-local, not UTC


def test_ap_range():
    from datetime import datetime

    d = datetime(2026, 10, 8)
    assert ap_range(d.replace(hour=10), d.replace(hour=10, minute=45)) == "10 - 10:45 a.m."
    assert ap_range(d.replace(hour=11), d.replace(hour=12)) == "11 a.m. - 12 p.m."
    assert ap_range(d.replace(hour=12, minute=30), d.replace(hour=13, minute=30)) == "12:30 - 1:30 p.m."
