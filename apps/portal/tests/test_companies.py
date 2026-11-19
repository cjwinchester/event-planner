import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse

from apps.commerce.claiming import claim_contacts
from apps.commerce.models import Company, CompanyContact


def account(django_user_model, email, verified=True):
    user = django_user_model.objects.create_user(email, None)
    EmailAddress.objects.create(user=user, email=email, verified=verified, primary=True)
    return user


@pytest.fixture
def acme(db):
    company = Company.objects.create(name="Acme Data", slug="acme-data")
    CompanyContact.objects.create(company=company, name="Dana Lead", email="Dana@Acme.example", can_manage=True)
    CompanyContact.objects.create(company=company, name="Viv Viewer", email="viv@acme.example", can_manage=False)
    return company


@pytest.mark.django_db
class TestCompanyAccess:
    def test_contacts_claimed_by_verified_email_only(self, acme, django_user_model):
        dana = account(django_user_model, "dana@acme.example")
        assert claim_contacts(dana) == 1
        assert CompanyContact.objects.get(name="Dana Lead").user == dana
        viv = account(django_user_model, "viv@acme.example", verified=False)
        assert claim_contacts(viv) == 0

    def test_home_shows_companies_without_speaker_profile(self, client, acme, django_user_model):
        client.force_login(account(django_user_model, "dana@acme.example"))
        body = client.get(reverse("portal:home")).content.decode()
        assert "Acme Data" in body and "create a speaker profile" in body

    def test_speaker_who_is_also_a_contact_sees_both(self, client, world, acme, django_user_model):
        user = account(django_user_model, "alice@example.com")  # world.alice's email
        CompanyContact.objects.create(company=acme, name="Alice Analyst", email="alice@example.com")
        client.force_login(user)
        body = client.get(reverse("portal:home")).content.decode()
        assert "Acme Data" in body and "How you appear in schedules" in body and "Alice Analyst" in body

    def test_strangers_and_view_only(self, client, acme, django_user_model):
        client.force_login(account(django_user_model, "someone@else.example"))
        assert client.get(reverse("portal:company", args=["acme-data"])).status_code == 404
        client.force_login(account(django_user_model, "viv@acme.example"))
        assert client.get(reverse("portal:company", args=["acme-data"])).status_code == 200
        assert client.get(reverse("portal:company-edit", args=["acme-data"])).status_code == 403
        resp = client.post(
            reverse("portal:company-contacts", args=["acme-data"]), {"name": "X", "email": "x@x.example"}
        )
        assert resp.status_code == 403 and acme.contacts.count() == 2

    def test_manager_edits_profile_and_colleagues(self, client, acme, django_user_model):
        client.force_login(account(django_user_model, "dana@acme.example"))
        client.post(
            reverse("portal:company-edit", args=["acme-data"]),
            {"name": "Acme Data Co", "website": "https://acme.example", "description": "We make data tools."},
        )
        acme.refresh_from_db()
        assert acme.name == "Acme Data Co" and acme.slug == "acme-data"
        url = reverse("portal:company-contacts", args=["acme-data"])
        client.post(url, {"name": "New Person", "email": "new@acme.example", "title": "Marketing"})
        new = acme.contacts.get(email="new@acme.example")
        assert not new.can_manage and new.user is None
        assert client.post(url, {"name": "Dup", "email": "NEW@acme.example"}).status_code == 422
        viv = acme.contacts.get(name="Viv Viewer")
        client.post(reverse("portal:company-contact-update", args=["acme-data", viv.pk]), {"action": "grant_manage"})
        viv.refresh_from_db()
        assert viv.can_manage

    def test_last_manager_cannot_leave(self, client, acme, django_user_model):
        client.force_login(account(django_user_model, "dana@acme.example"))
        dana = acme.contacts.get(name="Dana Lead")
        update = reverse("portal:company-contact-update", args=["acme-data", dana.pk])
        client.post(update, {"action": "remove"})
        assert acme.contacts.filter(pk=dana.pk).exists()
        viv = acme.contacts.get(name="Viv Viewer")
        client.post(reverse("portal:company-contact-update", args=["acme-data", viv.pk]), {"action": "grant_manage"})
        resp = client.post(update, {"action": "remove"})
        assert resp["Location"] == reverse("portal:home") and not acme.contacts.filter(pk=dana.pk).exists()
