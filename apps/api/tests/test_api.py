import pytest
from rest_framework.test import APIClient

from conftest import at


@pytest.fixture
def api(world):
    def as_user(user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    return as_user


@pytest.mark.django_db
class TestTenancy:
    def test_sessions_scoped_to_org(self, api, world, make_session):
        make_session("Ours")
        assert [s["title"] for s in api(world.staff_user).get("/api/v1/sessions/").json()["results"]] == ["Ours"]
        assert api(world.outsider).get("/api/v1/sessions/").json()["results"] == []

    def test_cannot_create_in_other_orgs_event(self, api, world):
        resp = api(world.outsider).post("/api/v1/sessions/", {"event": world.event.pk, "title": "Sneaky"})
        assert resp.status_code == 400 and "event" in resp.json()

    def test_viewer_cannot_write(self, api, world, make_session):
        session = make_session("Ours")
        resp = api(world.viewer_user).patch(f"/api/v1/sessions/{session.pk}/", {"title": "x"})
        assert resp.status_code == 403

    def test_calendar_shows_network_events_to_other_orgs(self, api, world):
        world.event.start_date = world.event.start_date.replace(year=2099)
        world.event.end_date = world.event.end_date.replace(year=2099)
        world.event.save()
        names = [e["name"] for e in api(world.outsider).get("/api/v1/calendar/").json()["results"]]
        assert names == ["NICAR 2027"]

    def test_contract_free_event_payload(self, api, world):
        data = api(world.staff_user).get(f"/api/v1/events/{world.event.pk}/").json()
        assert "fnb_minimum_inclusive" not in data


@pytest.mark.django_db
class TestPlanning:
    def test_place_endpoint_returns_409_on_conflict(self, api, world, make_session):
        make_session("First", room=world.er_a, start=at(5, 9), end=at(5, 10))
        second = make_session("Second")
        body = {"room": world.er_a.pk, "starts_at": at(5, 9).isoformat(), "ends_at": at(5, 10).isoformat()}
        resp = api(world.staff_user).post(f"/api/v1/sessions/{second.pk}/place/", body, format="json")
        assert resp.status_code == 409
        assert resp.json()["conflicts"][0]["kind"] == "room_overbook"
        body["room"] = world.er_c.pk
        resp = api(world.staff_user).post(f"/api/v1/sessions/{second.pk}/place/", body, format="json")
        assert resp.status_code == 200 and resp.json()["saved"]

    def test_grid(self, api, world, make_session):
        make_session("Placed", room=world.er_a, start=at(5, 9), end=at(5, 10))
        make_session("Pool")
        grid = api(world.staff_user).get(f"/api/v1/events/{world.event.pk}/grid/").json()
        assert [r["name"] for r in grid["rooms"]] == ["Plaza A", "Plaza B", "Plaza C", "Plaza A-B"]
        assert [i["title"] for i in grid["items"]] == ["Placed"]
        assert [i["title"] for i in grid["unscheduled"]] == ["Pool"]

    def test_publish_is_queued(self, api, world, make_session):
        make_session("Placed", room=world.er_a, start=at(5, 9), end=at(5, 10))
        resp = api(world.staff_user).post(f"/api/v1/events/{world.event.pk}/publish/", {}, format="json")
        assert resp.status_code == 202  # ImmediateBackend runs it inline in tests
        assert world.event.publish_runs.get().status in ("succeeded", "partial")  # partial: no PDF libs


@pytest.mark.django_db
class TestSpeakerSelfService:
    def test_claim_profile_update_and_manage_grants(self, api, world, django_user_model):
        user = django_user_model.objects.create_user("alice@example.com", "pw")
        client = api(user)
        me = client.get("/api/v1/speakers/me/").json()
        assert me["email"] == "alice@example.com"  # claimed by email; sees own private fields
        assert client.patch("/api/v1/speakers/me/", {"pronouns": "she/her"}, format="json").status_code == 200
        client.post("/api/v1/speakers/me/grants/", {"organization": world.other_org.pk}, format="json")
        outsider_view = api(world.outsider).get(f"/api/v1/speakers/{world.alice.pk}/").json()
        assert outsider_view["email"] == "alice@example.com"
        client.delete(f"/api/v1/speakers/me/grants/{world.other_org.pk}/")
        assert "email" not in api(world.outsider).get(f"/api/v1/speakers/{world.alice.pk}/").json()

    def test_submit_pitch_and_hide_queued_decision(self, api, world, django_user_model):
        from datetime import timedelta

        from django.utils import timezone

        world.event.pitch_window_opens_at = timezone.now() - timedelta(days=1)
        world.event.pitch_window_closes_at = timezone.now() + timedelta(days=1)
        world.event.save()
        user = django_user_model.objects.create_user("alice@example.com", "pw")
        client = api(user)
        client.get("/api/v1/speakers/me/")
        resp = client.post(
            "/api/v1/my-pitches/",
            {"title": "Scraping", "description": "D", "events": [world.event.pk]},
            format="json",
        )
        assert resp.status_code == 201, resp.json()
        target_id = world.event.pitch_targets.get().pk
        staff = api(world.staff_user)
        staff.post("/api/v1/pitch-reviews/decide/", {"ids": [target_id], "decision": "accept_queue"}, format="json")
        assert client.get("/api/v1/my-pitches/").json()["results"][0]["targets"][0]["status"] == "submitted"
        staff.post("/api/v1/pitch-reviews/inform/", {"ids": [target_id]}, format="json")
        assert client.get("/api/v1/my-pitches/").json()["results"][0]["targets"][0]["status"] == "accepted"


@pytest.mark.django_db
def test_ask_without_provider_is_501(api, world):
    resp = api(world.staff_user).post("/api/v1/ask/", {"organization": world.org.pk, "question": "hi"}, format="json")
    assert resp.status_code == 501


@pytest.mark.django_db
def test_openapi_schema_renders(client):
    assert client.get("/api/schema/").status_code == 200
