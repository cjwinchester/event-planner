import json

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.events.models import Concession, VenueRFP
from apps.publishing.bundle import Audience, build_event_bundle


@pytest.fixture
def rfp(world):
    rfp = VenueRFP.objects.create(event=world.event, venue=world.venue, title="RFP Grand Hotel", status="sent")
    Concession.objects.create(event=world.event, rfp=rfp, category="rooms", request="1 comp per 40 room nights")
    Concession.objects.create(
        event=world.event,
        rfp=rfp,
        category="av",
        request="Free Wi-Fi in meeting space",
        outcome=Concession.Outcome.PARTIAL,
        granted_terms="Free in 4 of 8 rooms",
        notes="Revisit for 2028",
    )
    return rfp


@pytest.mark.django_db
def test_concessions_are_private(world, rfp):
    for audience in Audience:
        text = json.dumps(build_event_bundle(world.event, audience))
        assert "comp per 40" not in text and "RFP Grand Hotel" not in text
    outsider = APIClient()
    outsider.force_authenticate(world.outsider)
    assert outsider.get("/api/v1/concessions/").json()["results"] == []
    assert outsider.get("/api/v1/rfps/").json()["results"] == []
    staff = APIClient()
    staff.force_authenticate(world.staff_user)
    outcomes = {c["outcome"] for c in staff.get(f"/api/v1/concessions/?rfp={rfp.pk}").json()["results"]}
    assert outcomes == {"pending", "partial"}


@pytest.mark.django_db
def test_concession_must_match_rfp_event(world, rfp):
    from datetime import date

    from apps.events.models import Event

    other = Event.objects.create(
        organization=world.org,
        name="Other",
        slug="other",
        start_date=date(2028, 1, 1),
        end_date=date(2028, 1, 2),
        timezone="UTC",
    )
    staff = APIClient()
    staff.force_authenticate(world.staff_user)
    resp = staff.post("/api/v1/concessions/", {"event": other.pk, "rfp": rfp.pk, "request": "x"}, format="json")
    assert resp.status_code == 400 and "rfp" in resp.json()


@pytest.mark.django_db
def test_admin_inline_sets_event_from_rfp(client, world, rfp):
    client.force_login(world.staff_user)
    url = reverse("admin:events_venuerfp_change", args=[rfp.pk])
    page = client.get(url)
    assert page.status_code == 200 and "Partially granted" in page.content.decode()
    data = {
        "event": world.event.pk,
        "venue": world.venue.pk,
        "title": rfp.title,
        "status": "responded",
        "concessions-TOTAL_FORMS": "1",
        "concessions-INITIAL_FORMS": "0",
        "concessions-0-category": "fnb",
        "concessions-0-request": "10% off F&B minimum",
        "concessions-0-outcome": "denied",
        "concessions-0-sort_order": "0",
    }
    resp = client.post(url, data)
    assert resp.status_code == 302, resp.content.decode()[:2000]
    new = Concession.objects.get(request="10% off F&B minimum")
    assert new.event == world.event and new.outcome == "denied"
