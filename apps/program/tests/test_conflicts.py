import pytest
from django.core.exceptions import ValidationError

from apps.program.conflicts import Kind, Severity, check_session, event_conflicts
from apps.program.models import LogisticsItem, SessionSpeaker
from apps.program.scheduling import PlacementError, place_logistics_item, place_session
from conftest import at


@pytest.mark.django_db
class TestRoomConflicts:
    def test_overlapping_sessions_in_same_room_conflict(self, world, make_session):
        make_session("First", room=world.er_a, start=at(5, 9), end=at(5, 10))
        second = make_session("Second")
        result = place_session(second, room=world.er_a, starts_at=at(5, 9, 30), ends_at=at(5, 10, 30))
        assert not result.ok and not result.saved
        assert [c.kind for c in result.errors] == [Kind.ROOM]

    def test_back_to_back_is_fine(self, world, make_session):
        make_session("First", room=world.er_a, start=at(5, 9), end=at(5, 10))
        second = make_session("Second")
        result = place_session(second, room=world.er_a, starts_at=at(5, 10), ends_at=at(5, 11))
        assert result.ok and result.saved
        second.refresh_from_db()
        assert second.room == world.er_a

    def test_combined_room_conflicts_with_its_parts(self, world, make_session):
        make_session("In B", room=world.er_b, start=at(5, 9), end=at(5, 10))
        big = make_session("Keynote")
        result = place_session(big, room=world.er_ab, starts_at=at(5, 9), ends_at=at(5, 10))
        assert [c.kind for c in result.errors] == [Kind.ROOM]
        # ...but not with an unrelated room
        assert place_session(big, room=world.er_c, starts_at=at(5, 9), ends_at=at(5, 10)).ok

    def test_canceled_sessions_do_not_block(self, world, make_session):
        make_session("Gone", room=world.er_a, start=at(5, 9), end=at(5, 10), status="canceled")
        assert place_session(make_session("New"), room=world.er_a, starts_at=at(5, 9), ends_at=at(5, 10)).ok

    def test_blocking_logistics_conflicts_but_water_service_does_not(self, world, make_session):
        make_session("Panel", room=world.er_a, start=at(5, 9), end=at(5, 10))
        water = LogisticsItem.objects.create(event=world.event, category=world.water, title="Water")
        assert place_logistics_item(water, room=world.er_a, starts_at=at(5, 9), ends_at=at(5, 10)).saved
        desk = LogisticsItem.objects.create(event=world.event, category=world.registration, title="Reg desk")
        result = place_logistics_item(desk, room=world.er_a, starts_at=at(5, 9), ends_at=at(5, 10))
        assert not result.ok and result.errors[0].kind == Kind.ROOM

    def test_model_clean_blocks_overbooking(self, world, make_session):
        make_session("First", room=world.er_a, start=at(5, 9), end=at(5, 10))
        dup = make_session("Second", room=world.er_a, start=at(5, 9), end=at(5, 10))  # create() skips clean
        with pytest.raises(ValidationError):
            dup.full_clean()

    def test_room_from_other_event_rejected(self, world, make_session):
        from apps.events.models import Event, EventRoom, EventVenue

        other = Event.objects.create(
            organization=world.org,
            name="Other",
            slug="other",
            start_date=world.event.start_date,
            end_date=world.event.end_date,
            timezone="America/New_York",
        )
        EventVenue.objects.create(event=other, venue=world.venue)
        foreign_room = EventRoom.objects.create(event=other, room=world.room_c)
        with pytest.raises(PlacementError):
            place_session(make_session(), room=foreign_room, starts_at=at(5, 9), ends_at=at(5, 10))


@pytest.mark.django_db
class TestSpeakerConflicts:
    def test_speaker_double_booked(self, world, make_session):
        make_session("A", room=world.er_a, start=at(5, 9), end=at(5, 10), speakers=[world.alice])
        b = make_session("B", speakers=[world.alice])
        result = place_session(b, room=world.er_c, starts_at=at(5, 9, 15), ends_at=at(5, 9, 45))
        assert [c.kind for c in result.errors] == [Kind.SPEAKER]

    def test_adding_conflicting_speaker_is_invalid(self, world, make_session):
        make_session("A", room=world.er_a, start=at(5, 9), end=at(5, 10), speakers=[world.alice])
        b = make_session("B", room=world.er_c, start=at(5, 9), end=at(5, 10))
        with pytest.raises(ValidationError):
            SessionSpeaker(session=b, speaker=world.alice).full_clean()

    def test_different_speakers_are_fine(self, world, make_session):
        make_session("A", room=world.er_a, start=at(5, 9), end=at(5, 10), speakers=[world.alice])
        b = make_session("B", speakers=[world.bob])
        assert place_session(b, room=world.er_c, starts_at=at(5, 9), ends_at=at(5, 10)).ok


@pytest.mark.django_db
class TestWarningsAndSweep:
    def test_plenary_overlap_is_warning_only(self, world, make_session):
        make_session("Keynote", room=world.er_ab, start=at(5, 9), end=at(5, 10), is_plenary=True)
        s = make_session("Breakout")
        result = place_session(s, room=world.er_c, starts_at=at(5, 9), ends_at=at(5, 10))
        assert result.saved
        assert [c.kind for c in result.warnings] == [Kind.PLENARY]

    def test_capacity_warning(self, world, make_session):
        s = make_session("Big", room=world.er_a, start=at(5, 9), end=at(5, 10), expected_headcount=250)
        assert [c.kind for c in check_session(s)] == [Kind.CAPACITY]

    def test_event_sweep_reports_each_pair_once(self, world, make_session):
        make_session("A", room=world.er_a, start=at(5, 9), end=at(5, 10), speakers=[world.alice])
        make_session("B", room=world.er_a, start=at(5, 9), end=at(5, 10), speakers=[world.alice])
        make_session("C", room=world.er_c, start=at(5, 11), end=at(5, 12))
        conflicts = event_conflicts(world.event)
        assert sorted(c.kind for c in conflicts) == [Kind.ROOM, Kind.SPEAKER]
        assert all(c.severity == Severity.ERROR for c in conflicts)

    def test_dry_run_does_not_save(self, world, make_session):
        s = make_session("Preview")
        result = place_session(s, room=world.er_a, starts_at=at(5, 9), ends_at=at(5, 10), dry_run=True)
        s.refresh_from_db()
        assert result.ok and not result.saved and s.room is None
