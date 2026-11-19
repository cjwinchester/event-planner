"""Audit #2: deals are private between one organization and the company. An
organization can never give anyone access to another organization's deals."""

from decimal import Decimal

import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse
from rest_framework.test import APIClient

from apps.commerce.access import add_contact_for_org
from apps.commerce.models import Company, CompanyContact, Sponsorship
from apps.events.models import Event


def login(client, django_user_model, email):
    user = django_user_model.objects.create_user(email, None)
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    client.force_login(user)
    return user


@pytest.fixture
def two_orgs(world):
    """Acme sponsors an IRE event and an Other-org event."""
    acme = Company.objects.create(name="Acme", slug="acme")
    other_event = Event.objects.create(organization=world.other_org, name="Other conf", slug="other-conf",
                                       start_date=world.event.start_date, end_date=world.event.end_date,
                                       timezone="UTC")  # fmt: skip
    ire_deal = Sponsorship.objects.create(event=world.event, company=acme, status="paid", amount=Decimal("9000"))
    other_deal = Sponsorship.objects.create(event=other_event, company=acme, status="paid", amount=Decimal("1234"))
    return world, acme, ire_deal, other_deal


@pytest.mark.django_db
class TestDealScope:
    def test_org_added_manager_sees_only_that_orgs_deals(self, client, two_orgs, django_user_model):
        world, acme, ire_deal, other_deal = two_orgs
        contact = add_contact_for_org(acme, world.other_org, name="Planted", email="planted@x.example", manager=True)
        assert contact.can_manage and not contact.company_wide  # Acme also deals with IRE: no company-wide
        login(client, django_user_model, "planted@x.example")
        body = client.get(reverse("portal:company", args=["acme"])).content.decode()
        assert "Other conf" in body and world.event.name not in body
        assert client.get(reverse("portal:sponsorship", args=["acme", ire_deal.pk])).status_code == 404
        assert client.get(reverse("portal:sponsorship", args=["acme", other_deal.pk])).status_code == 200

    def test_first_manager_is_company_wide_only_without_other_orgs(self, world):
        solo = Company.objects.create(name="Solo", slug="solo")
        Sponsorship.objects.create(event=world.event, company=solo, status="committed")
        first = add_contact_for_org(solo, world.org, name="First", email="first@solo.example", manager=True)
        assert first.company_wide
        second = add_contact_for_org(solo, world.org, name="Second", email="second@solo.example", manager=True)
        assert not second.company_wide  # only the first manager

    def test_same_person_added_by_two_orgs_gains_both_scopes(self, two_orgs):
        world, acme, *_ = two_orgs
        a = add_contact_for_org(acme, world.org, name="Pat", email="pat@acme.example", manager=True)
        b = add_contact_for_org(acme, world.other_org, name="Pat", email="PAT@acme.example", manager=True)
        assert a.pk == b.pk and set(b.organizations.all()) == {world.org, world.other_org}

    def test_scoped_manager_limits(self, client, two_orgs, django_user_model):
        world, acme, *_ = two_orgs
        wide = CompanyContact.objects.create(company=acme, name="CEO", email="ceo@acme.example", company_wide=True)
        add_contact_for_org(acme, world.org, name="Scoped", email="scoped@acme.example", manager=True)
        login(client, django_user_model, "scoped@acme.example")
        assert client.get(reverse("portal:company-edit", args=["acme"])).status_code == 403
        update = reverse("portal:company-contact-update", args=["acme", wide.pk])
        assert client.post(update, {"action": "remove"}).status_code == 403
        data = {"name": "Helper", "email": "helper@acme.example", "can_manage": "on", "company_wide": "on"}
        client.post(reverse("portal:company-contacts", args=["acme"]), data)
        helper = acme.contacts.get(email="helper@acme.example")
        assert not helper.company_wide and set(helper.organizations.all()) == {world.org}

    def test_company_wide_manager_sees_all_and_edits_profile(self, client, two_orgs, django_user_model):
        _world, acme, ire_deal, other_deal = two_orgs
        CompanyContact.objects.create(company=acme, name="CEO", email="ceo@acme.example", company_wide=True)
        login(client, django_user_model, "ceo@acme.example")
        assert client.get(reverse("portal:sponsorship", args=["acme", ire_deal.pk])).status_code == 200
        assert client.get(reverse("portal:sponsorship", args=["acme", other_deal.pk])).status_code == 200
        client.post(
            reverse("portal:company-edit", args=["acme"]), {"name": "Acme Inc", "website": "", "description": ""}
        )
        acme.refresh_from_db()
        assert acme.name == "Acme Inc"

    def test_orgs_cannot_edit_a_claimed_companys_profile(self, two_orgs, django_user_model):
        world, acme, *_ = two_orgs
        api = APIClient()
        api.force_authenticate(world.staff_user)
        assert api.patch(f"/api/v1/companies/{acme.pk}/", {"description": "fix"}, format="json").status_code == 200
        ceo = django_user_model.objects.create_user("ceo@acme.example", None)
        CompanyContact.objects.create(company=acme, name="CEO", email="ceo@acme.example", company_wide=True, user=ceo)
        assert api.patch(f"/api/v1/companies/{acme.pk}/", {"description": "nope"}, format="json").status_code == 403

    def test_admin_contact_inline_is_read_only_for_staff(self, client, two_orgs):
        world, acme, *_ = two_orgs
        world.staff_user.is_staff = True
        world.staff_user.save()
        client.force_login(world.staff_user)
        page = client.get(reverse("admin:commerce_company_change", args=[acme.pk])).content.decode()
        assert "contacts-TOTAL_FORMS" not in page or 'name="contacts-0-email"' not in page

    def test_staff_page_adds_scoped_contacts(self, client, two_orgs):
        world, acme, *_ = two_orgs
        client.force_login(world.staff_user)
        client.post(reverse("planner:sponsors-action", args=[world.org.slug, world.event.slug]),
                    {"action": "add_contact", "company": acme.pk, "name": "Dana", "email": "dana@acme.example",
                     "can_manage": "on"})  # fmt: skip
        dana = acme.contacts.get(email="dana@acme.example")
        assert dana.can_manage and not dana.company_wide and list(dana.organizations.all()) == [world.org]
