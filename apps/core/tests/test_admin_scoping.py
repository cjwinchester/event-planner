"""The admin is the phase-1 staff UI, so tenancy has to hold there."""

import pytest
from django.urls import reverse

from conftest import at


@pytest.mark.django_db
def test_staff_only_see_their_org_sessions(client, world, make_session):
    from apps.events.models import Event

    other_event = Event.objects.create(
        organization=world.other_org,
        name="Secret conf",
        slug="secret",
        start_date=world.event.start_date,
        end_date=world.event.end_date,
        timezone="UTC",
    )
    make_session("Ours")
    make_session("Theirs", event=other_event)
    client.force_login(world.staff_user)
    body = client.get(reverse("admin:program_session_changelist")).content.decode()
    assert "Ours" in body and "Theirs" not in body


@pytest.mark.django_db
def test_cannot_open_other_orgs_object(client, world, make_session):
    session = make_session("Ours")
    client.force_login(world.outsider)
    resp = client.get(reverse("admin:program_session_change", args=[session.pk]))
    assert resp.status_code in (302, 404)  # admin redirects to index for objects outside the queryset


@pytest.mark.django_db
def test_viewer_cannot_edit(client, world, make_session):
    session = make_session("Ours")
    client.force_login(world.viewer_user)
    url = reverse("admin:program_session_change", args=[session.pk])
    assert client.get(url).status_code == 200
    resp = client.post(url, {"title": "Hacked"})
    assert resp.status_code == 403


@pytest.mark.django_db
def test_contracts_never_visible_to_other_org(client, world):
    from apps.events.models import EventContract

    EventContract.objects.create(event=world.event, title="JW contract", attendee_room_rate=199)
    client.force_login(world.outsider)
    body = client.get(reverse("admin:events_eventcontract_changelist")).content.decode()
    assert "JW contract" not in body


@pytest.mark.django_db
def test_admin_save_blocks_room_overbook(client, world, make_session):
    make_session("First", room=world.er_a, start=at(5, 9), end=at(5, 10))
    second = make_session("Second")
    client.force_login(world.staff_user)
    url = reverse("admin:program_session_change", args=[second.pk])
    form = client.get(url).context["adminform"].form
    data = {k: v for k, v in form.initial.items() if v is not None and not isinstance(v, list)}
    data.update(
        {
            "event": world.event.pk,
            "title": "Second",
            "room": world.er_a.pk,
            "starts_at_0": "2027-03-05",
            "starts_at_1": "09:30",
            "ends_at_0": "2027-03-05",
            "ends_at_1": "10:30",
            "extra": "{}",
            "session_speakers-TOTAL_FORMS": "0",
            "session_speakers-INITIAL_FORMS": "0",
            "assets-TOTAL_FORMS": "0",
            "assets-INITIAL_FORMS": "0",
        }
    )
    resp = client.post(url, data)
    assert resp.status_code == 200  # re-rendered with errors
    assert "Room is already booked" in resp.content.decode()
    second.refresh_from_db()
    assert second.room is None
