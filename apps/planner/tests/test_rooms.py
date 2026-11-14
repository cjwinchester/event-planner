import pytest
from django.urls import reverse

from apps.events.models import Event, EventRoom, EventVenue
from apps.program import scheduling
from apps.program.models import Session
from apps.venues.models import Room, Venue
from conftest import at


def url(world, name="rooms", *args):
    return reverse(f"planner:{name}", args=[world.org.slug, world.event.slug, *args])


@pytest.fixture
def virtual(world):
    """The test event as a virtual event with three Zoom rooms and no columns yet."""
    world.event.format = Event.Format.VIRTUAL
    world.event.save()
    EventRoom.objects.filter(event=world.event).delete()
    venue = Venue.objects.create(name="IRE Zoom", kind=Venue.Kind.VIRTUAL, owner=world.org)
    rooms = [
        Room.objects.create(venue=venue, name=f"Zoom Room {i}", host_account=f"room{i}@ire.example", sort_order=i)
        for i in (1, 2)
    ]
    rooms.append(
        Room.objects.create(
            venue=venue, name="Zoom Webinar", host_account="webinar@ire.example", meeting_type="webinar", sort_order=0
        )
    )
    return venue, rooms


@pytest.mark.django_db
class TestFillRooms:
    def test_fills_free_rooms_webinar_last_and_reports_leftovers(self, world, virtual, make_session):
        venue, rooms = virtual
        EventVenue.objects.create(event=world.event, venue=venue)
        cols = [EventRoom.objects.create(event=world.event, room=r, sort_order=i) for i, r in enumerate(rooms)]
        webinar = cols[2]
        a = make_session("A", start=at(5, 10), end=at(5, 11))
        b = make_session("B", start=at(5, 10), end=at(5, 11))
        c = make_session("C", start=at(5, 10, 30), end=at(5, 11, 30))
        d = make_session("D", start=at(5, 10), end=at(5, 11))
        clash = make_session("E", start=at(5, 13), end=at(5, 14), speakers=[world.alice])
        make_session("F", start=at(5, 13), end=at(5, 14), speakers=[world.alice])
        canceled = make_session("G", start=at(5, 15), end=at(5, 16), status=Session.Status.CANCELED)

        result = scheduling.fill_rooms(world.event)
        for s in (a, b, c, d, clash, canceled):
            s.refresh_from_db()
        assert {a.room, b.room} == {cols[0], cols[1]}
        assert d.room == webinar  # meeting rooms were full, so the webinar room is used last
        assert c.room is None and canceled.room is None
        reasons = dict((s.title, r) for s, r in result.skipped)
        assert reasons["C"] == "No free room at that time."
        assert "Alice" in reasons["F"]  # speaker clash, not "no room"

    def test_no_rooms(self, world, virtual, make_session):
        make_session("A", start=at(5, 10), end=at(5, 11))
        assert scheduling.fill_rooms(world.event).skipped[0][1] == "This event has no rooms."


@pytest.mark.django_db
class TestRoomsPanel:
    def test_add_all_standing_rooms_attaches_venue(self, client, world, virtual):
        venue, rooms = virtual
        client.force_login(world.staff_user)
        assert "Add all 3" in client.get(url(world)).content.decode()
        resp = client.post(url(world), {"all_standing": "1"})
        assert resp["HX-Trigger"] == "grid:refresh"
        assert EventVenue.objects.filter(event=world.event, venue=venue, role="virtual").exists()
        # Standing rooms keep their own order (the webinar room is sort_order 0 here).
        assert [er.room for er in world.event.event_rooms.order_by("sort_order")] == [rooms[2], rooms[0], rooms[1]]

    def test_new_virtual_room_creates_private_venue(self, client, world):
        client.force_login(world.staff_user)
        resp = client.post(
            url(world, "room-new"), {"name": "Zoom Room 9", "meeting_type": "meeting", "host_account": "z9@ire.example"}
        )
        assert resp.status_code == 200
        room = Room.objects.get(name="Zoom Room 9")
        assert room.venue.owner == world.org and room.venue.is_virtual
        assert world.event.event_rooms.filter(room=room).exists()
        dup = client.post(url(world, "room-new"), {"name": "zoom room 9", "meeting_type": "meeting"})
        assert dup.status_code == 422

    def test_edit_move_remove(self, client, world, make_session):
        client.force_login(world.staff_user)
        client.post(url(world, "room-move", world.er_b.pk), {"direction": "up"})
        order = list(world.event.event_rooms.order_by("sort_order").values_list("pk", flat=True))
        assert order[:2] == [world.er_b.pk, world.er_a.pk]
        client.post(
            url(world, "room-edit", world.er_c.pk),
            {"er-display_name": "Salon C", "er-is_recorded": "on", "er-is_schedulable": "on"},
        )
        world.er_c.refresh_from_db()
        assert world.er_c.display_name == "Salon C" and world.er_c.is_recorded and not world.er_c.is_public
        make_session("Busy", room=world.er_a, start=at(5, 10), end=at(5, 11))
        assert client.post(url(world, "room-remove", world.er_a.pk)).status_code == 422
        assert client.post(url(world, "room-remove", world.er_c.pk)).status_code == 200
        assert not EventRoom.objects.filter(pk=world.er_c.pk).exists()

    def test_fill_and_undo_through_panel(self, client, world, make_session):
        s = make_session("Timed", start=at(5, 10), end=at(5, 11))
        client.force_login(world.staff_user)
        body = client.post(url(world, "rooms-fill"), {"day": "2027-03-05"}).content.decode()
        s.refresh_from_db()
        assert "Placed 1 session." in body and s.room == world.er_a
        client.post(url(world, "rooms-unfill"), {"placed": [f"{s.pk}:{world.er_a.pk}"]})
        s.refresh_from_db()
        assert s.room is None

    def test_permissions(self, client, world, virtual):
        client.force_login(world.viewer_user)
        assert client.get(url(world)).status_code == 200
        assert client.post(url(world), {"all_standing": "1"}).status_code == 403
        assert client.post(url(world, "rooms-fill")).status_code == 403
        client.force_login(world.outsider)
        assert client.get(url(world)).status_code == 403


@pytest.mark.django_db
class TestVirtualVenuesArePrivate:
    def test_other_org_cannot_see_or_attach(self, client, world, virtual):
        venue, rooms = virtual
        from rest_framework.test import APIClient

        api = APIClient()
        api.force_authenticate(world.outsider)
        names = {v["name"] for v in api.get("/api/v1/venues/").json()["results"]}
        assert "Grand Hotel" in names and "IRE Zoom" not in names
        assert api.get(f"/api/v1/rooms/{rooms[0].pk}/").status_code == 404
        api.force_authenticate(world.staff_user)
        room = api.get(f"/api/v1/rooms/{rooms[0].pk}/").json()
        assert room["host_account"] == "room1@ire.example"

        client.force_login(world.outsider)
        assert client.get(reverse("admin:venues_venue_change", args=[venue.pk])).status_code in (302, 404)

        other_event = Event.objects.create(
            organization=world.other_org, name="Other", slug="other", start_date=world.event.start_date,
            end_date=world.event.end_date, timezone="UTC",
        )  # fmt: skip
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            EventVenue(event=other_event, venue=venue).full_clean()

    def test_meeting_details_need_an_owned_venue(self, world):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            Room(venue=world.venue, name="Zoom in the shared hotel", host_account="x@y.z").full_clean()
        with pytest.raises(ValidationError):
            Venue(name="Unowned Zoom", kind=Venue.Kind.VIRTUAL).full_clean()

    def test_join_links_stay_out_of_public_bundle(self, world, virtual, make_session):
        from apps.publishing.bundle import Audience, build_event_bundle

        venue, rooms = virtual
        world.event.format = Event.Format.IN_PERSON
        world.event.save()
        rooms[0].join_url = "https://zoom.example/j/123"
        rooms[0].save()
        EventVenue.objects.create(event=world.event, venue=venue)
        er = EventRoom.objects.create(event=world.event, room=rooms[0])
        make_session("Zoomed", room=er, start=at(5, 10), end=at(5, 11))
        public = build_event_bundle(world.event, Audience.PUBLIC)
        internal = build_event_bundle(world.event, Audience.INTERNAL)
        assert "zoom.example" not in str(public)
        assert (
            next(s for s in internal["sessions"] if s["title"] == "Zoomed")["live_url"] == "https://zoom.example/j/123"
        )
