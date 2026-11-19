import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse

from apps.attendees.models import AttendeePass
from apps.program.models import SessionSpeaker
from apps.speakers import sharing
from apps.speakers.merging import claim_profiles, merge_profiles
from apps.speakers.models import EventSpeaker, SpeakerProfile, SpeakerShareGrant

Source = SpeakerShareGrant.Source


def verified_user(django_user_model, email):
    user = django_user_model.objects.create_user(email, None)
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    return user


@pytest.mark.django_db
class TestMerge:
    def test_moves_links_dedupes_and_fills_blanks(self, world, make_session):
        old = SpeakerProfile.objects.create(first_name="Al", last_name="A", phone="555")  # imports carry no email
        new = SpeakerProfile.objects.create(first_name="Alice", last_name="A", email="al@example.com")
        both = make_session("Both on it", speakers=[old, new])
        only_old = make_session("Only old", speakers=[old])
        EventSpeaker.objects.create(event=world.event, speaker=old, notes="Staff note from last year")
        EventSpeaker.objects.create(event=world.event, speaker=new)
        merge_profiles(new, [old])
        old.refresh_from_db()
        new.refresh_from_db()
        assert old.merged_into == new and new.first_name == "Alice" and new.phone == "555"  # latest wins, blanks filled
        assert SessionSpeaker.objects.filter(session=both).count() == 1
        assert SessionSpeaker.objects.get(session=only_old).speaker == new
        assert EventSpeaker.objects.get(event=world.event).notes == "Staff note from last year"

    def test_speaker_revocation_wins(self, world):
        a = SpeakerProfile.objects.create(first_name="A")
        b = SpeakerProfile.objects.create(first_name="B", email="x@example.com")
        sharing.grant(a, world.org, Source.PITCH)
        sharing.revoke(a, world.org, by_speaker=True)
        sharing.grant(b, world.org, Source.CREATED)
        merge_profiles(b, [a])
        assert not sharing.can_view_private(world.org, b)

    def test_duplicate_pass_keeps_working(self, world):
        a = SpeakerProfile.objects.create(first_name="A")
        b = SpeakerProfile.objects.create(first_name="B")
        AttendeePass.objects.create(event=world.event, speaker=a, name="A")
        AttendeePass.objects.create(event=world.event, speaker=b, name="B")
        merge_profiles(b, [a])
        assert AttendeePass.objects.count() == 2 and AttendeePass.objects.filter(speaker=b).count() == 1


@pytest.mark.django_db
class TestClaim:
    def test_claims_by_verified_email_case_insensitive(self, world, django_user_model):
        p = SpeakerProfile.objects.create(first_name="Pat", email="Pat@Example.com")
        user = verified_user(django_user_model, "pat@example.com")
        assert claim_profiles(user) == p
        assert SpeakerProfile.objects.get(pk=p.pk).user == user

    def test_second_verified_email_merges_into_linked_profile(self, world, django_user_model):
        user = verified_user(django_user_model, "pat@example.com")
        mine = SpeakerProfile.objects.create(first_name="Pat", email="pat@example.com", user=user)
        work = SpeakerProfile.objects.create(first_name="Pat", bio="From work", email="pat@paper.example")
        EmailAddress.objects.create(user=user, email="pat@paper.example", verified=True)
        assert claim_profiles(user) == mine
        work.refresh_from_db()
        mine.refresh_from_db()
        assert work.merged_into == mine and mine.bio == "From work"

    def test_unverified_email_claims_nothing(self, world, django_user_model):
        SpeakerProfile.objects.create(first_name="Pat", email="pat@example.com")
        user = django_user_model.objects.create_user("pat@example.com", None)
        EmailAddress.objects.create(user=user, email="pat@example.com", verified=False)
        assert claim_profiles(user) is None


@pytest.mark.django_db
class TestPortal:
    def test_home_routing(self, client, world, django_user_model):
        assert client.get("/")["Location"].startswith(reverse("account_login"))
        client.force_login(world.staff_user)
        assert client.get("/")["Location"] == reverse("planner:event-list")
        client.force_login(verified_user(django_user_model, "new@example.com"))
        assert client.get("/")["Location"] == reverse("portal:home")

    def test_new_speaker_creates_profile(self, client, world, django_user_model):
        client.force_login(verified_user(django_user_model, "new@example.com"))
        assert client.get(reverse("portal:home"))["Location"] == reverse("portal:profile")
        body = client.get(reverse("portal:profile")).content.decode()
        assert "Create your speaker profile" in body and "Admin" not in body
        data = {
            "first_name": "Nia", "last_name": "New", "bio": "Reporter.", "email": "new@example.com",
            "country": "us", "links-TOTAL_FORMS": "2", "links-INITIAL_FORMS": "0",
            "links-0-kind": "website", "links-0-url": "https://nia.example", "links-1-kind": "website",
        }  # fmt: skip
        resp = client.post(reverse("portal:profile"), data)
        assert resp.status_code == 302
        p = SpeakerProfile.objects.get(email="new@example.com")
        assert p.country == "US" and p.links.get().url == "https://nia.example"
        assert "Nia New" in client.get(reverse("portal:home")).content.decode()

    def test_sharing_grant_and_revoke(self, client, world, django_user_model):
        user = verified_user(django_user_model, "alice@example.com")
        client.force_login(user)
        claim_profiles(user)
        client.post(reverse("portal:sharing"), {"organization": world.org.pk})
        assert sharing.can_view_private(world.org, world.alice)
        client.post(reverse("portal:sharing-revoke", args=[world.org.pk]))
        assert not sharing.can_view_private(world.org, world.alice)
        sharing.grant(world.alice, world.org, Source.ENGAGEMENT)  # automatic grants don't undo a revocation
        assert not sharing.can_view_private(world.org, world.alice)
        assert "you turned it off" in client.get(reverse("portal:sharing")).content.decode()

    def test_only_own_profile(self, client, world, django_user_model):
        client.force_login(verified_user(django_user_model, "bob@example.com"))
        client.post(reverse("portal:sharing-revoke", args=[world.org.pk]))  # no profile: nothing to revoke
        assert SpeakerShareGrant.objects.count() == 0
        assert client.get(reverse("portal:profile")).status_code == 200

    def test_login_page_offers_email_code(self, client, world):
        body = client.get(reverse("account_login")).content.decode()
        assert reverse("account_request_login_code") in body


@pytest.mark.django_db
def test_signup_without_password_and_staff_password_login(client, world, django_user_model):
    body = client.get(reverse("account_login")).content.decode()
    assert 'name="password"' in body  # staff can still use passwords
    resp = client.post(reverse("account_signup"), {"email": "nopass@example.com"})
    assert resp.status_code == 302
    user = django_user_model.objects.get(email="nopass@example.com")
    assert not user.has_usable_password()
