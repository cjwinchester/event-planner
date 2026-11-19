import pytest
from django.urls import reverse

from apps.commerce.models import SponsorshipLevel
from apps.events.models import Event, EventVenue
from apps.organizations.models import Membership
from apps.pitches.models import FormQuestion
from apps.program.models import LogisticsItem, TimeSlot
from apps.venues.models import Room, Venue
from conftest import at

URL = reverse("planner:event-new")


def post_data(world, **kw):
    data = {
        "organization": world.org.pk, "name": "NICAR 2028", "short_name": "NICAR28", "slug": "nicar-2028",
        "kind": "conference", "format": "in_person", "start_date": "2028-03-02", "end_date": "2028-03-05",
        "timezone": "America/Chicago",
    }  # fmt: skip
    data.update(kw)
    return data


@pytest.mark.django_db
class TestNewEvent:
    def test_dashboard_button_and_prefill(self, client, world):
        client.force_login(world.staff_user)
        assert "+ New event" in client.get(reverse("planner:event-list")).content.decode()
        body = client.get(URL, {"copy_from": world.event.pk}).content.decode()
        assert f'value="{world.event.name.replace("2027", "2028")}"' in body  # next year's edition suggested
        assert "Copy these parts" in body and "Time slots" in body

    def test_from_scratch(self, client, world):
        client.force_login(world.staff_user)
        resp = client.post(URL, post_data(world, new_series="NICAR"))
        event = Event.objects.get(slug="nicar-2028")
        assert resp.status_code == 302 and resp["Location"].endswith("/ire/nicar-2028/grid/")
        assert event.series.name == "NICAR" and not event.event_rooms.exists()

    def test_copy_selected_parts(self, client, world, make_session):
        src = world.event  # 2027-03-04 (Thu) .. 03-07, America/New_York
        TimeSlot.objects.create(event=src, starts_at=at(4, 9), ends_at=at(4, 10), label="Opening")
        TimeSlot.objects.create(event=src, starts_at=at(7, 9), ends_at=at(7, 10), label="Last day")
        make_session("Stock thing", is_stock=True)
        make_session("One-off")
        LogisticsItem.objects.create(event=src, category=world.registration, title="Registration desk",
                                     room=world.er_a, starts_at=at(5, 8), ends_at=at(5, 17))  # fmt: skip
        FormQuestion.objects.create(event=src, slug="beat", prompt="Your beat?")
        SponsorshipLevel.objects.create(event=src, name="Gold", rank=1)
        client.force_login(world.staff_user)
        parts = ["rooms", "time_slots", "tracks", "stock_sessions", "logistics", "pitch_questions"]
        # The new event is 3 days (one shorter), so the source's day-4 slot is dropped.
        client.post(URL, post_data(world, copy_from=src.pk, parts=parts, end_date="2028-03-04"))
        new = Event.objects.get(slug="nicar-2028")
        assert new.event_rooms.count() == src.event_rooms.count()
        slots = list(new.time_slots.all())
        assert [s.label for s in slots] == ["Opening"]
        assert (
            slots[0].starts_at.astimezone(new.tz).isoformat() == "2028-03-02T09:00:00-06:00"
        )  # day 1, same local time
        assert list(new.sessions.values_list("title", flat=True)) == ["Stock thing"]
        item = new.logistics_items.get()
        assert item.room.room == world.room_a and item.starts_at.astimezone(new.tz).date().isoformat() == "2028-03-03"
        assert new.form_questions.count() == 1 and not new.sponsorship_levels.exists()  # not ticked
        assert set(new.tracks.values_list("slug", flat=True)) == {"ai", "public-records"}

    def test_never_copies_other_orgs_private_venue(self, world):
        from apps.events.copying import copy_event_parts

        theirs = Venue.objects.create(name="Their Zoom", kind=Venue.Kind.VIRTUAL, owner=world.other_org)
        Room.objects.create(venue=theirs, name="Their room")
        EventVenue.objects.create(event=world.event, venue=theirs)  # bypassing clean, as bad data would
        target = Event.objects.create(organization=world.org, name="T", slug="t", start_date=world.event.start_date,
                                      end_date=world.event.end_date, timezone="UTC")  # fmt: skip
        copy_event_parts(world.event, target, ["rooms"])
        assert not target.event_venues.filter(venue=theirs).exists()

    def test_validation_and_permissions(self, client, world):
        client.force_login(world.staff_user)
        assert client.post(URL, post_data(world, slug=world.event.slug)).status_code == 200  # slug taken
        assert client.post(URL, post_data(world, end_date="2028-03-01")).status_code == 200
        assert not Event.objects.filter(slug="nicar-2028").exists()
        other = Event.objects.create(
            organization=world.other_org, name="X", slug="x", start_date=world.event.start_date,
            end_date=world.event.end_date, timezone="UTC",
        )  # fmt: skip
        assert client.post(URL, post_data(world, copy_from=other.pk, parts=["rooms"])).status_code == 200
        client.force_login(world.viewer_user)
        assert client.get(URL).status_code == 403
        assert "+ New event" not in client.get(reverse("planner:event-list")).content.decode()


def test_next_edition():
    from apps.planner.event_views import next_edition

    assert next_edition("NICAR 2026") == "NICAR 2027"
    assert next_edition("nicar-2026") == "nicar-2027"
    assert next_edition("NICAR26") == "NICAR27"
    assert next_edition("Watchdog Workshop") == "Watchdog Workshop"


@pytest.mark.django_db
def test_staff_of_two_orgs_pick_organization(client, world):
    Membership.objects.create(user=world.staff_user, organization=world.other_org, role=Membership.Role.STAFF)
    client.force_login(world.staff_user)
    assert '<select name="organization"' in client.get(URL).content.decode()
