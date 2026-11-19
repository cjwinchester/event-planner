from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.hotel.models import HotelLink, ScheduleChange, default_cutoff
from apps.program import scheduling
from apps.program.models import Session
from conftest import at


@pytest.fixture
def tracking(world):
    """Change tracking already on (cutoff in the past)."""
    world.event.hotel_changes_from = timezone.now() - timedelta(hours=1)
    world.event.save()
    return world


def open_changes(world):
    return list(ScheduleChange.objects.filter(event=world.event, done_at=None))


@pytest.mark.django_db
class TestRecording:
    def test_default_cutoff_is_day_before(self, world):
        cutoff = default_cutoff(world.event)
        assert cutoff.astimezone(world.event.tz).isoformat() == "2027-03-03T00:00:00-05:00"

    def test_nothing_recorded_before_cutoff(self, world, make_session):
        s = make_session("Early", room=world.er_a, start=at(5, 10), end=at(5, 11))
        s.title = "Early, renamed"
        s.save()
        assert not ScheduleChange.objects.exists()  # 2027 event: still before the day-before default

    def test_title_change_consolidates_and_reverts(self, tracking, make_session):
        world = tracking
        s = make_session("Data 101", room=world.er_a, start=at(5, 10), end=at(5, 11))
        ScheduleChange.objects.all().delete()  # its creation counts as "added"; start clean
        s.title = "Data 101: Spreadsheets"
        s.save()
        s.title = "Data 101: Spreadsheets & SQL"
        s.save()
        (row,) = open_changes(world)
        assert row.before["title"] == "Data 101" and row.after["title"] == "Data 101: Spreadsheets & SQL"
        assert row.title_changed and row.kind == "changed"
        s.title = "Data 101"
        s.save()
        assert open_changes(world) == []  # back to what the boards show

    def test_moves_adds_and_removals(self, tracking, make_session):
        world = tracking
        s = make_session("Panel", room=world.er_a, start=at(5, 10), end=at(5, 11))
        (added,) = open_changes(world)
        assert added.kind == "added"
        ScheduleChange.objects.update(done_at=timezone.now())
        scheduling.place_session(s, room=world.er_c, starts_at=at(5, 14), ends_at=at(5, 15))
        (moved,) = open_changes(world)
        assert moved.kind == "changed" and set(moved.changed_fields()) == {"room", "starts_at", "ends_at"}
        ScheduleChange.objects.update(done_at=timezone.now())
        s.status = Session.Status.CANCELED
        s.save()
        (removed,) = open_changes(world)
        assert removed.kind == "removed" and removed.before["room"] == "Plaza C"
        ScheduleChange.objects.update(done_at=timezone.now())
        other = make_session("Doomed", room=world.er_b, start=at(5, 9), end=at(5, 10))
        ScheduleChange.objects.update(done_at=timezone.now())
        other_pk = other.pk
        other.delete()
        assert ScheduleChange.objects.get(session_key=other_pk, done_at=None).kind == "removed"

    def test_new_change_after_done_opens_new_row(self, tracking, make_session):
        world = tracking
        s = make_session("Panel", room=world.er_a, start=at(5, 10), end=at(5, 11))
        ScheduleChange.objects.update(done_at=timezone.now())
        s.title = "Panel II"
        s.save()
        assert len(open_changes(world)) == 1 and ScheduleChange.objects.count() == 2

    def test_deleting_an_event_does_not_record(self, tracking, make_session):
        world = tracking
        make_session("Panel", room=world.er_a, start=at(5, 10), end=at(5, 11))
        world.event.delete()
        assert not ScheduleChange.objects.exists()


@pytest.mark.django_db
class TestHotelPage:
    def test_page_shows_changes_first_and_every_block_with_details(self, client, tracking, make_session):
        world = tracking
        a = make_session("Opening", room=world.er_a, start=at(5, 9), end=at(5, 10), av_requirements="Two lavs")
        b = make_session("Records", room=world.er_b, start=at(5, 9), end=at(5, 10))
        ScheduleChange.objects.update(done_at=timezone.now())
        scheduling.place_session(b, room=world.er_b, starts_at=at(5, 11), ends_at=at(5, 12))
        a.title = "Opening keynote"
        a.save()
        link = HotelLink.objects.create(event=world.event, label="Grand Hotel banquets")
        resp = client.get(reverse("hotel:home", args=[link.token]))
        body = resp.content.decode()
        assert resp["Cache-Control"] == "private, no-store"
        assert body.index("New title") < body.index("Moved")  # title changes first
        assert "<del>Opening</del>" in body and "Opening keynote" in body
        assert "Plaza A" in body and "Plaza B" in body and "Two lavs" in body
        link.refresh_from_db()
        assert link.view_count == 1

    def test_venue_link_shows_only_that_venues_rooms(self, client, world, make_session):
        from apps.events.models import EventRoom, EventVenue
        from apps.venues.models import Room, Venue

        annex = Venue.objects.create(name="Annex", city="Denver")
        EventVenue.objects.create(event=world.event, venue=annex)
        ballroom = EventRoom.objects.create(event=world.event, room=Room.objects.create(venue=annex, name="Annex Hall"))
        make_session("Main room talk", room=world.er_a, start=at(5, 9), end=at(5, 10))
        make_session("Annex talk", room=ballroom, start=at(5, 9), end=at(5, 10))
        link = HotelLink.objects.create(event=world.event, venue=annex, label="Annex")
        body = client.get(reverse("hotel:home", args=[link.token])).content.decode()
        assert "Annex talk" in body and "Main room talk" not in body

    def test_revoked_and_expired_links(self, client, world):
        link = HotelLink.objects.create(event=world.event, label="H")
        assert link.expires_at.date().isoformat() == "2027-03-15"  # event ends Mar 7: +7 days, end of day
        link.revoked_at = timezone.now()
        link.save()
        assert client.get(reverse("hotel:home", args=[link.token])).status_code == 404
        other = HotelLink.objects.create(event=world.event, label="H2", expires_at=timezone.now() - timedelta(days=1))
        assert client.get(reverse("hotel:home", args=[other.token])).status_code == 404


@pytest.mark.django_db
class TestStaffPage:
    def url(self, world, name="hotel"):
        return reverse(f"planner:{name}", args=[world.org.slug, world.event.slug])

    def test_create_link_and_only_admins_mark_done(self, client, tracking, make_session):
        world = tracking
        make_session("Panel", room=world.er_a, start=at(5, 10), end=at(5, 11))
        client.force_login(world.staff_user)
        client.post(self.url(world, "hotel-action"), {"action": "create", "label": "Grand Hotel"})
        link = HotelLink.objects.get()
        assert link.token in client.get(self.url(world)).content.decode()
        change = ScheduleChange.objects.get()
        client.post(self.url(world, "hotel-action"), {"action": "done", "change": change.pk})
        change.refresh_from_db()
        assert change.done_at is None  # staff can't
        client.force_login(world.admin_user)
        client.post(self.url(world, "hotel-action"), {"action": "done", "change": change.pk})
        change.refresh_from_db()
        assert change.done_at and change.done_by == world.admin_user

    def test_permissions_and_cutoff(self, client, world):
        client.force_login(world.viewer_user)
        assert client.get(self.url(world)).status_code == 403
        client.force_login(world.outsider)
        assert client.get(self.url(world)).status_code == 403
        client.force_login(world.staff_user)
        client.post(self.url(world, "hotel-action"), {"action": "cutoff", "cutoff": "2027-03-01T08:00"})
        world.event.refresh_from_db()
        assert world.event.hotel_changes_from.astimezone(world.event.tz).isoformat() == "2027-03-01T08:00:00-05:00"


@pytest.mark.django_db
def test_board_includes_every_placed_logistics_item(client, world, make_session):
    """Everything in a room at a time shows, public or not (a coffee setup, a staff-only room block)."""
    from apps.program.models import LogisticsItem

    make_session("Morning panel", room=world.er_a, start=at(5, 10), end=at(5, 11))
    LogisticsItem.objects.create(event=world.event, category=world.water, title="Coffee service", room=world.er_a,
                                 starts_at=at(5, 9), ends_at=at(5, 10), fnb_requirements="Coffee for 150",
                                 is_public=False)  # fmt: skip
    LogisticsItem.objects.create(event=world.event, category=world.registration, title="Unplaced desk")
    link = HotelLink.objects.create(event=world.event, label="H")
    body = client.get(reverse("hotel:home", args=[link.token])).content.decode()
    assert "Coffee service" in body and "Coffee for 150" in body and "Water service" in body
    assert body.index("Coffee service") < body.index("Morning panel")  # 9:00 before 10:00 in the same room
    assert "Unplaced desk" not in body
