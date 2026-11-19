import pytest
from django.core.cache import cache

from apps.publishing.cache import content_version, public_bundle
from conftest import at


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def published(world, make_session):
    world.event.publish_schedule = True
    world.event.save()
    s = make_session("Cached panel", room=world.er_a, start=at(5, 10), end=at(5, 11), speakers=[world.alice])
    return world, s


@pytest.mark.django_db
class TestPublicBundleCache:
    def test_served_from_cache_until_content_changes(self, published, django_assert_max_num_queries):
        world, s = published
        public_bundle(world.event)
        with django_assert_max_num_queries(10):  # version check only, no rebuild
            assert public_bundle(world.event)["sessions"][0]["title"] == "Cached panel"
        s.title = "Renamed panel"
        s.save()
        assert public_bundle(world.event)["sessions"][0]["title"] == "Renamed panel"

    def test_speaker_confirmation_changes_version(self, published):
        world, s = published
        before = content_version(world.event)
        row = s.session_speakers.get()
        row.is_confirmed = False
        row.save()
        assert content_version(world.event) != before
        assert public_bundle(world.event)["sessions"][0]["speakers"] == []

    def test_callers_get_independent_copies(self, published):
        world, _ = published
        first = public_bundle(world.event)
        first["sessions"][0]["live_url"] = "https://example.org/my/SECRET/join/1/"
        assert "live_url" not in public_bundle(world.event)["sessions"][0]


@pytest.mark.django_db
def test_pass_pages_never_share_personal_links(client, published):
    """Each attendee's join links carry their own token, even though the bundle is cached."""
    from django.urls import reverse

    from apps.attendees.models import AttendeePass
    from apps.program.models import VirtualMeeting

    world, s = published
    VirtualMeeting.objects.create(session=s, external_id="1", join_url="https://zoom.example/j/1",
                                  host_account="h", starts_at=s.starts_at, minutes=60)  # fmt: skip
    a = AttendeePass.objects.create(event=world.event, name="A")
    b = AttendeePass.objects.create(event=world.event, name="B")
    page_a = client.get(reverse("attendees:home", args=[a.token])).content.decode()
    page_b = client.get(reverse("attendees:home", args=[b.token])).content.decode()
    assert a.token in page_a and b.token not in page_a
    assert b.token in page_b and a.token not in page_b
