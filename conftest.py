"""Shared pytest fixtures: a small, realistic org/event/venue world.

`world` builds two organizations (so tests can prove isolation), a venue
with plain and combined rooms, an event with EventRooms, lookups and
speakers. Factories (`make_session`, ...) cover the rest.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("America/New_York")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """An aware datetime on 2027-03-0{day} in the test event's timezone."""
    return datetime.combine(date(2027, 3, day), time(hour, minute), tzinfo=TZ)


class World:
    pass


@pytest.fixture
def world(db, django_user_model):
    from apps.events.models import Event, EventRoom, EventSeries, EventVenue
    from apps.organizations.models import Membership, Organization
    from apps.program.models import LogisticsCategory, SessionType, SkillLevel, Track
    from apps.speakers.models import SpeakerProfile
    from apps.venues.models import Room, RoomCapacity, Venue

    w = World()
    w.org = Organization.objects.create(name="Investigative Reporters", slug="ire")
    w.other_org = Organization.objects.create(name="Other Nonprofit", slug="other")

    w.admin_user = django_user_model.objects.create_user("admin@ire.org", "pw")
    w.staff_user = django_user_model.objects.create_user("staff@ire.org", "pw")
    w.viewer_user = django_user_model.objects.create_user("viewer@ire.org", "pw")
    w.outsider = django_user_model.objects.create_user("staff@other.org", "pw")
    Membership.objects.create(user=w.admin_user, organization=w.org, role=Membership.Role.ADMIN)
    Membership.objects.create(user=w.staff_user, organization=w.org, role=Membership.Role.STAFF)
    Membership.objects.create(user=w.viewer_user, organization=w.org, role=Membership.Role.VIEWER)
    Membership.objects.create(user=w.outsider, organization=w.other_org, role=Membership.Role.ADMIN)
    for u in (w.admin_user, w.staff_user, w.viewer_user, w.outsider):
        u.refresh_from_db()

    w.venue = Venue.objects.create(name="Grand Hotel", city="Denver", region="CO", timezone="America/Denver")
    w.room_a = Room.objects.create(venue=w.venue, name="Plaza A")
    w.room_b = Room.objects.create(venue=w.venue, name="Plaza B")
    w.room_c = Room.objects.create(venue=w.venue, name="Plaza C")
    w.room_ab = Room.objects.create(venue=w.venue, name="Plaza A-B")
    w.room_ab.parts.set([w.room_a, w.room_b])
    RoomCapacity.objects.create(room=w.room_a, setup="theater", capacity=100)

    w.series = EventSeries.objects.create(organization=w.org, name="NICAR", slug="nicar")
    w.event = Event.objects.create(
        organization=w.org,
        series=w.series,
        name="NICAR 2027",
        slug="nicar-2027",
        short_name="NICAR27",
        start_date=date(2027, 3, 4),
        end_date=date(2027, 3, 7),
        timezone="America/New_York",
        publish_schedule=True,
    )
    EventVenue.objects.create(event=w.event, venue=w.venue)
    w.er_a = EventRoom.objects.create(event=w.event, room=w.room_a, sort_order=1, setup="theater")
    w.er_b = EventRoom.objects.create(event=w.event, room=w.room_b, sort_order=2, is_recorded=True)
    w.er_c = EventRoom.objects.create(event=w.event, room=w.room_c, sort_order=3)
    w.er_ab = EventRoom.objects.create(event=w.event, room=w.room_ab, sort_order=4)

    w.panel = SessionType.objects.create(organization=w.org, name="Panel")
    w.hands_on = SessionType.objects.create(organization=w.org, name="Hands-on", is_hands_on=True)
    w.beginner = SkillLevel.objects.create(organization=w.org, name="Beginner")
    w.track_ai = Track.objects.create(event=w.event, name="AI", slug="ai")
    w.track_records = Track.objects.create(event=w.event, name="Public records", slug="public-records")
    w.registration = LogisticsCategory.objects.create(organization=w.org, name="Registration", public_by_default=True)
    w.water = LogisticsCategory.objects.create(organization=w.org, name="Water service", blocks_room=False)

    w.alice = SpeakerProfile.objects.create(
        first_name="Alice", last_name="Analyst", affiliation="Daily News", email="alice@example.com"
    )
    w.bob = SpeakerProfile.objects.create(first_name="Bob", last_name="Builder", affiliation="Data Desk")
    return w


@pytest.fixture
def make_session(world):
    from apps.program.models import Session, SessionSpeaker

    def _make(title="A session", *, room=None, start=None, end=None, speakers=(), edited=True, **kw):
        session = Session.objects.create(
            event=kw.pop("event", world.event),
            title=title,
            room=room,
            starts_at=start,
            ends_at=end,
            title_status=Session.EditStatus.EDITED if edited else Session.EditStatus.PENDING,
            **kw,
        )
        for i, sp in enumerate(speakers):
            SessionSpeaker.objects.create(session=session, speaker=sp, sort_order=i)
        return session

    return _make
