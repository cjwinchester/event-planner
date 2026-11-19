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


@pytest.fixture
def deals(world, acme):
    from datetime import date
    from decimal import Decimal

    from apps.commerce.models import (
        ExhibitorBooking,
        ExhibitorPackage,
        Invoice,
        Sponsorship,
        SponsorshipDeliverable,
        SponsorshipLevel,
    )  # fmt: skip
    from apps.pitches.models import FormQuestion

    gold = SponsorshipLevel.objects.create(event=world.event, name="Gold", benefits="Logo on site\nTwo comps")
    sp = Sponsorship.objects.create(
        event=world.event, company=acme, level=gold, status="contracted", amount=Decimal("5000")
    )
    SponsorshipDeliverable.objects.create(sponsorship=sp, title="Logo (SVG)", due_date=date(2027, 2, 1))
    Invoice.objects.create(event=world.event, sponsorship=sp, amount=Decimal("5000"), status="sent",
                           payment_url="https://pay.example/inv1")  # fmt: skip
    Invoice.objects.create(event=world.event, sponsorship=sp, amount=Decimal("1"), status="draft")
    pkg = ExhibitorPackage.objects.create(event=world.event, name="Nonprofit table")
    booth = ExhibitorBooking.objects.create(event=world.event, company=acme, package=pkg, status="committed")
    other = Company.objects.create(name="Prospect Co", slug="prospect-co")
    Sponsorship.objects.create(event=world.event, company=acme, status="prospect", amount=Decimal("99999"))
    FormQuestion.objects.create(event=world.event, form="exhibitor", slug="power", prompt="Need power?",
                                kind="boolean")  # fmt: skip
    FormQuestion.objects.create(event=world.event, form="exhibitor", slug="tables", prompt="How many tables?",
                                kind="integer", required=True)  # fmt: skip
    return world, sp, booth, other


@pytest.mark.django_db
class TestCompanyDeals:
    def test_overview_hides_prospects_and_labels_status(self, client, deals, django_user_model):
        _world, sp, _booth, _ = deals
        client.force_login(account(django_user_model, "viv@acme.example"))
        body = client.get(reverse("portal:company", args=["acme-data"])).content.decode()
        assert "Gold sponsorship" in body and "Confirmed" in body and "1 item to send" in body
        assert "Nonprofit table" in body and "99999" not in body
        page = client.get(reverse("portal:sponsorship", args=["acme-data", sp.pk])).content.decode()
        assert "Logo on site" in page and "https://pay.example/inv1" in page and "$1.00" not in page  # drafts hidden
        assert "Only contacts who can manage" in page

    def test_upload_deliverable(self, client, deals, django_user_model):
        from django.core.files.uploadedfile import SimpleUploadedFile

        _world, sp, *_ = deals
        client.force_login(account(django_user_model, "dana@acme.example"))
        item = sp.deliverables.get()
        url = reverse("portal:sponsorship", args=["acme-data", sp.pk])
        client.post(url, {"deliverable": item.pk, "file": SimpleUploadedFile("logo.svg", b"<svg/>", "image/svg+xml")})
        item.refresh_from_db()
        assert item.status == "received" and item.file.name.endswith(".svg")

    def test_view_only_contact_cannot_upload(self, client, deals, django_user_model):
        from django.core.files.uploadedfile import SimpleUploadedFile

        _world, sp, *_ = deals
        client.force_login(account(django_user_model, "viv@acme.example"))
        item = sp.deliverables.get()
        url = reverse("portal:sponsorship", args=["acme-data", sp.pk])
        resp = client.post(url, {"deliverable": item.pk, "file": SimpleUploadedFile("x.png", b"x")})
        assert resp.status_code == 403

    def test_exhibitor_answers_until_deadline(self, client, deals, django_user_model):
        from datetime import timedelta

        from django.utils import timezone

        world, _, booth, _ = deals
        client.force_login(account(django_user_model, "dana@acme.example"))
        url = reverse("portal:booth", args=["acme-data", booth.pk])
        assert "To be assigned" in client.get(url).content.decode()
        assert client.post(url, {"q_power": "on"}).status_code == 422  # tables is required
        client.post(url, {"q_power": "on", "q_tables": "2"})
        booth.refresh_from_db()
        assert booth.answers == {"power": True, "tables": 2}
        world.event.company_edit_deadline = timezone.now() - timedelta(minutes=1)
        world.event.save()
        client.post(url, {"q_tables": "9"})
        booth.refresh_from_db()
        assert booth.answers["tables"] == 2
        assert "deadline has passed" in client.get(url).content.decode()

    def test_other_companies_deals_are_off_limits(self, client, deals, django_user_model):
        from apps.commerce.models import Sponsorship

        world, *_, other = deals
        theirs = Sponsorship.objects.create(event=world.event, company=other, status="paid")
        client.force_login(account(django_user_model, "dana@acme.example"))
        assert client.get(reverse("portal:sponsorship", args=["acme-data", theirs.pk])).status_code == 404
        assert client.get(reverse("portal:sponsorship", args=["prospect-co", theirs.pk])).status_code == 404


@pytest.mark.django_db
def test_deliverable_upload_rejects_html_and_huge_files(client, deals, django_user_model):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.portal import forms as portal_forms

    _world, sp, *_ = deals
    client.force_login(account(django_user_model, "dana@acme.example"))
    item = sp.deliverables.get()
    url = reverse("portal:sponsorship", args=["acme-data", sp.pk])
    page = client.post(url, {"deliverable": item.pk, "file": SimpleUploadedFile("x.html", b"<script>")}, follow=True)
    item.refresh_from_db()
    assert item.status == "pending" and "not allowed" in page.content.decode()
    portal_forms.MAX_DELIVERABLE_MB = 0
    try:
        client.post(url, {"deliverable": item.pk, "file": SimpleUploadedFile("big.pdf", b"x" * 10)})
    finally:
        portal_forms.MAX_DELIVERABLE_MB = 20
    item.refresh_from_db()
    assert item.status == "pending"
