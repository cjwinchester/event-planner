from datetime import date

import pytest

from apps.events.models import Event, EventRoom, EventVenue
from apps.program.copying import copy_sessions
from conftest import at


@pytest.fixture
def next_year(world):
    event = Event.objects.create(
        organization=world.org,
        series=world.series,
        name="NICAR 2028",
        slug="nicar-2028",
        start_date=date(2028, 3, 2),
        end_date=date(2028, 3, 5),
        timezone="America/New_York",
    )
    EventVenue.objects.create(event=event, venue=world.venue)
    EventRoom.objects.create(event=event, room=world.room_a)
    return event


@pytest.mark.django_db
def test_copies_only_stock_sessions_with_tracks(world, make_session, next_year):
    stock = make_session("Excel 1", is_stock=True, speakers=[world.alice])
    stock.tracks.set([world.track_ai])
    make_session("One-off")
    result = copy_sessions(world.event, next_year)
    assert [s.title for s in result.created] == ["Excel 1"]
    copy = result.created[0]
    assert copy.copied_from == stock and copy.title_status == "pending" and copy.source == "stock"
    assert list(copy.tracks.values_list("slug", flat=True)) == ["ai"]
    assert copy.session_speakers.count() == 0  # speakers opt-in
    # running again skips the duplicate title
    assert copy_sessions(world.event, next_year).skipped


@pytest.mark.django_db
def test_keep_schedule_shifts_days_and_maps_rooms(world, make_session, next_year):
    make_session("Excel 1", is_stock=True, room=world.er_a, start=at(5, 9), end=at(5, 10))
    copy = copy_sessions(world.event, next_year, keep_schedule=True).created[0]
    local = copy.starts_at.astimezone(next_year.tz)
    assert (local.date(), local.hour) == (date(2028, 3, 3), 9)  # day 2 of the event, same wall clock
    assert copy.room.room == world.room_a


@pytest.mark.django_db
def test_cross_org_copy_refused(world, next_year):
    next_year.organization = world.other_org
    with pytest.raises(ValueError):
        copy_sessions(world.event, next_year)
