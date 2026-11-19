import csv
import io
from decimal import Decimal

import pytest
from django.core import mail
from django.urls import reverse

from apps.commerce.models import (
    Company,
    CompanyContact,
    ExhibitorBooking,
    Sponsorship,
    SponsorshipDeliverable,
    SponsorshipLevel,
)  # fmt: skip
from apps.pitches.models import FormQuestion


@pytest.fixture
def sponsor_world(world):
    acme = Company.objects.create(name="Acme Data", slug="acme-data")
    gold = SponsorshipLevel.objects.create(event=world.event, name="Gold")
    sp = Sponsorship.objects.create(
        event=world.event, company=acme, level=gold, status="contracted", amount=Decimal("5000")
    )
    booth = ExhibitorBooking.objects.create(event=world.event, company=acme, booth_number="7", answers={"power": True})
    FormQuestion.objects.create(event=world.event, form="exhibitor", slug="power", prompt="Need power?", kind="boolean")
    elsewhere = Company.objects.create(name="Elsewhere Inc", slug="elsewhere")
    return world, acme, sp, booth, elsewhere


def url(world, name="sponsors"):
    return reverse(f"planner:{name}", args=[world.org.slug, world.event.slug])


@pytest.mark.django_db
class TestSponsorsPage:
    def test_page_and_permissions(self, client, sponsor_world):
        world, *_ = sponsor_world
        client.force_login(world.staff_user)
        body = client.get(url(world)).content.decode()
        assert "Acme Data" in body and "Gold" in body and "1 of 1" in body and "$5,000.00" in body
        client.force_login(world.viewer_user)
        assert client.get(url(world)).status_code == 403
        client.force_login(world.outsider)
        assert client.get(url(world)).status_code == 403

    def test_add_contact_and_invite(self, client, sponsor_world):
        world, acme, *_ = sponsor_world
        client.force_login(world.staff_user)
        client.post(url(world, "sponsors-action"), {"action": "add_contact", "company": acme.pk, "name": "Dana",
                                                    "email": "dana@acme.example", "can_manage": "on"})  # fmt: skip
        contact = acme.contacts.get()
        assert contact.is_primary and contact.can_manage
        client.post(url(world, "sponsors-action"), {"action": "invite", "contact": contact.pk})
        contact.refresh_from_db()
        assert contact.invited_at and len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert msg.to == ["dana@acme.example"] and "Acme Data" in msg.subject
        assert "/accounts/login/code/" in msg.body and "sponsorship and exhibit booth" in msg.body
        client.post(url(world, "sponsors-action"), {"action": "invite_all", "company": [acme.pk]})
        assert len(mail.outbox) == 1  # already invited

    def test_cannot_touch_companies_without_a_deal_here(self, client, sponsor_world):
        world, *_, elsewhere = sponsor_world
        CompanyContact.objects.create(company=elsewhere, name="X", email="x@elsewhere.example")
        client.force_login(world.staff_user)
        resp = client.post(url(world, "sponsors-action"), {"action": "add_contact", "company": elsewhere.pk,
                                                           "name": "Y", "email": "y@elsewhere.example"})  # fmt: skip
        assert resp.status_code == 404
        x = elsewhere.contacts.get()
        assert client.post(url(world, "sponsors-action"), {"action": "invite", "contact": x.pk}).status_code == 404
        assert not mail.outbox

    def test_deliverables_and_deadline(self, client, sponsor_world):
        world, _acme, sp, *_ = sponsor_world
        client.force_login(world.staff_user)
        client.post(url(world, "sponsors-action"), {"action": "add_deliverable", "sponsorship": sp.pk,
                                                    "title": "Logo", "due_date": "2027-02-01"})  # fmt: skip
        item = SponsorshipDeliverable.objects.get()
        client.post(url(world, "sponsors-action"), {"action": "mark_done", "deliverable": item.pk})
        item.refresh_from_db()
        assert item.status == "done"
        client.post(url(world, "sponsors-action"), {"action": "deadline", "deadline": "2027-02-20T17:00"})
        world.event.refresh_from_db()
        assert world.event.company_edit_deadline.astimezone(world.event.tz).hour == 17

    def test_exhibitor_csv(self, client, sponsor_world):
        world, *_ = sponsor_world
        client.force_login(world.staff_user)
        rows = list(csv.reader(io.StringIO(client.get(url(world, "exhibitors-csv")).content.decode())))
        assert rows[0][-1] == "Need power?" and rows[1][0] == "Acme Data" and rows[1][-1] == "True"


@pytest.mark.django_db
def test_room_count_from_run_of_show(client, world, make_session):
    from apps.program.models import Session
    from conftest import at

    s = make_session("Counted", room=world.er_a, start=at(5, 10), end=at(5, 11))
    url = reverse("planner:room-count", args=[world.org.slug, world.event.slug, s.pk])
    client.force_login(world.viewer_user)
    assert client.post(url, {"room_count": "80"}).status_code == 403
    client.force_login(world.staff_user)
    assert (
        "Room count"
        in client.get(reverse("planner:run-of-show", args=[world.org.slug, world.event.slug])).content.decode()
    )
    assert client.post(url, {"room_count": "87"}).status_code == 200
    assert Session.objects.get(pk=s.pk).room_count == 87
    assert client.post(url, {"room_count": "lots"}).status_code == 422
    client.post(url, {"room_count": ""})
    assert Session.objects.get(pk=s.pk).room_count is None
