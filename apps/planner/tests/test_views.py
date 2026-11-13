import pytest
from django.urls import reverse

from apps.program.models import TimeSlot
from conftest import at


@pytest.fixture
def staff(client, world):
    client.force_login(world.staff_user)
    return client


def grid_url(world):
    return reverse("planner:grid", args=[world.org.slug, world.event.slug])


@pytest.mark.django_db
class TestAccess:
    def test_event_list_and_grid(self, staff, world):
        assert world.event.name in staff.get(reverse("planner:event-list")).content.decode()
        body = staff.get(grid_url(world)).content.decode()
        assert 'id="planner-config"' in body and '"canEdit": true' in body

    def test_outsider_forbidden(self, client, world):
        client.force_login(world.outsider)
        assert client.get(grid_url(world)).status_code == 403

    def test_viewer_sees_grid_readonly(self, client, world, make_session):
        client.force_login(world.viewer_user)
        assert '"canEdit": false' in client.get(grid_url(world)).content.decode()
        session = make_session("S")
        resp = client.post(reverse("planner:session-panel", args=[session.pk]), {"title": "x"})
        assert resp.status_code == 403

    def test_login_required(self, client, world):
        assert client.get(grid_url(world)).status_code == 302

    def test_login_page_uses_site_layout(self, client):
        body = client.get(reverse("account_login")).content.decode()
        assert "css/app.css" in body and 'class="auth-card"' in body


@pytest.mark.django_db
class TestSessionPanel:
    def test_quick_edit_triggers_grid_refresh(self, staff, world, make_session):
        session = make_session("Old")
        resp = staff.post(
            reverse("planner:session-panel", args=[session.pk]),
            {
                "title": "New title",
                "title_status": "edited",
                "description_status": "pending",
                "status": "planning",
                "tracks": [world.track_ai.pk],
            },
        )
        assert resp.status_code == 200 and resp["HX-Trigger"] == "grid:refresh"
        session.refresh_from_db()
        assert session.title == "New title" and list(session.tracks.all()) == [world.track_ai]

    def test_placement_form_blocks_conflict(self, staff, world, make_session):
        make_session("Booked", room=world.er_a, start=at(5, 9), end=at(5, 10))
        session = make_session("Mover")
        url = reverse("planner:session-place", args=[session.pk])
        data = {"room": world.er_a.pk, "day": "2027-03-05", "start": "09:30", "end": "10:30"}
        resp = staff.post(url, data)
        assert resp.status_code == 409 and "Room is already booked" in resp.content.decode()
        data["room"] = world.er_c.pk
        assert staff.post(url, data).status_code == 200
        session.refresh_from_db()
        assert session.room == world.er_c and session.starts_at == at(5, 9, 30)

    def test_partial_placement_rejected(self, staff, world, make_session):
        session = make_session("Mover")
        resp = staff.post(reverse("planner:session-place", args=[session.pk]), {"room": world.er_a.pk})
        assert resp.status_code == 422

    def test_add_speaker_checks_conflicts(self, staff, world, make_session):
        make_session("A", room=world.er_a, start=at(5, 9), end=at(5, 10), speakers=[world.alice])
        b = make_session("B", room=world.er_c, start=at(5, 9), end=at(5, 10))
        resp = staff.post(reverse("planner:session-speaker-add", args=[b.pk]), {"speaker": world.alice.pk})
        assert resp.status_code == 409 and not b.session_speakers.exists()
        resp = staff.post(reverse("planner:session-speaker-add", args=[b.pk]), {"speaker": world.bob.pk})
        assert resp.status_code == 200 and b.session_speakers.get().speaker == world.bob

    def test_speaker_search(self, staff, world, make_session):
        session = make_session("A")
        body = staff.get(reverse("planner:speaker-search", args=[session.pk]), {"q": "ali"}).content.decode()
        assert "Alice Analyst" in body

    def test_new_session_lands_in_pool(self, staff, world):
        url = reverse("planner:session-new", args=[world.org.slug, world.event.slug])
        assert staff.post(url, {"title": "Brand new", "session_type": world.panel.pk}).status_code == 200
        assert world.event.sessions.get(title="Brand new").room is None


@pytest.mark.django_db
class TestTimeSlots:
    def url(self, world, name, *extra):
        return reverse(f"planner:{name}", args=[world.org.slug, world.event.slug, *extra])

    def test_add_list_and_reject_overlap(self, staff, world):
        data = {"day": "2027-03-05", "start": "09:00", "end": "10:00", "label": "Block A", "kind": "content"}
        assert staff.post(self.url(world, "slots"), data).status_code == 200
        slot = TimeSlot.objects.get()
        assert slot.starts_at == at(5, 9) and slot.ends_at == at(5, 10)
        overlap = {**data, "start": "09:30", "end": "10:30", "label": "Overlap"}
        resp = staff.post(self.url(world, "slots"), overlap)
        assert resp.status_code == 422 and TimeSlot.objects.count() == 1
        assert "Block A" in staff.get(self.url(world, "slots"), {"day": "2027-03-05"}).content.decode()

    def test_edit_and_delete(self, staff, world):
        slot = TimeSlot.objects.create(event=world.event, starts_at=at(5, 9), ends_at=at(5, 10), label="A")
        data = {"day": "2027-03-05", "start": "09:15", "end": "10:15", "label": "A2", "kind": "content"}
        assert staff.post(self.url(world, "slot-edit", slot.pk), data).status_code == 200
        slot.refresh_from_db()
        assert slot.label == "A2" and slot.starts_at == at(5, 9, 15)
        assert staff.post(self.url(world, "slot-delete", slot.pk)).status_code == 200
        assert not TimeSlot.objects.exists()

    def test_copy_day_skips_overlaps(self, staff, world):
        TimeSlot.objects.create(event=world.event, starts_at=at(5, 9), ends_at=at(5, 10), label="Morning")
        TimeSlot.objects.create(event=world.event, starts_at=at(5, 11), ends_at=at(5, 12), label="Late")
        TimeSlot.objects.create(event=world.event, starts_at=at(6, 11, 30), ends_at=at(6, 12, 30), label="Existing")
        resp = staff.post(
            self.url(world, "slots-copy"), {"from_day": "2027-03-05", "to_days": ["2027-03-06", "2027-03-07"]}
        )
        assert "Copied 3 slots (1 skipped" in resp.content.decode()
        assert TimeSlot.objects.filter(label="Morning").count() == 3


@pytest.mark.django_db
def test_grid_api_payload_and_version(world, make_session):
    from rest_framework.test import APIClient

    c = APIClient()
    c.force_authenticate(world.staff_user)
    make_session("Placed", room=world.er_ab, start=at(5, 9), end=at(5, 10), speakers=[world.alice])
    data = c.get(f"/api/v1/events/{world.event.pk}/grid/").json()
    rooms = {r["name"]: r for r in data["rooms"]}
    assert set(rooms["Plaza A-B"]["atoms"]) == {world.room_a.pk, world.room_b.pk}
    assert data["items"][0]["speakers"][0]["name"] == "Alice Analyst"
    v1 = c.get(f"/api/v1/events/{world.event.pk}/grid-version/").json()["version"]
    assert v1 == data["version"]
    make_session("Another")
    assert c.get(f"/api/v1/events/{world.event.pk}/grid-version/").json()["version"] != v1
