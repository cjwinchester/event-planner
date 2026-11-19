"""Speaker profiles are a shared directory, but other organizations must not
be able to rewrite them, or take an unclaimed one over by changing its email."""

import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse
from rest_framework.test import APIClient

from apps.organizations.models import Membership
from apps.speakers import sharing
from apps.speakers.merging import claim_profiles
from apps.speakers.models import SpeakerProfile, SpeakerShareGrant


def api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def profiles(world, django_user_model):
    """An unclaimed profile IRE created, and a claimed one."""
    unclaimed = SpeakerProfile.objects.create(
        first_name="Una", last_name="Claimed", email="una@example.com", created_by_organization=world.org
    )
    sharing.grant(unclaimed, world.org, SpeakerShareGrant.Source.CREATED)
    owner = django_user_model.objects.create_user("owner@example.com", None)
    claimed = SpeakerProfile.objects.create(first_name="Cla", last_name="Imed", email="owner@example.com", user=owner)
    other_staff = django_user_model.objects.create_user("staff2@other.org", None)
    Membership.objects.create(user=other_staff, organization=world.other_org, role=Membership.Role.STAFF)
    return world, unclaimed, claimed, owner, other_staff


@pytest.mark.django_db
class TestSpeakerEditRules:
    def test_other_org_cannot_edit_or_take_over(self, profiles, django_user_model):
        _world, unclaimed, claimed, _owner, other_staff = profiles
        c = api(other_staff)
        assert c.patch(f"/api/v1/speakers/{claimed.pk}/", {"bio": "Defaced"}, format="json").status_code == 403
        assert c.patch(f"/api/v1/speakers/{unclaimed.pk}/", {"email": "me@other.org"}, format="json").status_code == 403
        unclaimed.refresh_from_db()
        assert unclaimed.email == "una@example.com"
        # The takeover this blocks: a login with the planted email would have claimed the profile.
        attacker = django_user_model.objects.create_user("me@other.org", None)
        EmailAddress.objects.create(user=attacker, email="me@other.org", verified=True)
        assert claim_profiles(attacker) is None

    def test_grant_org_can_edit_public_but_not_email(self, profiles):
        world, unclaimed, *_ = profiles
        sharing.grant(unclaimed, world.other_org, SpeakerShareGrant.Source.PITCH)
        unclaimed.created_by_organization = None
        unclaimed.save()
        _, _, _, _, other_staff = profiles
        c = api(other_staff)
        assert c.patch(f"/api/v1/speakers/{unclaimed.pk}/", {"bio": "Updated"}, format="json").status_code == 200
        assert c.patch(f"/api/v1/speakers/{unclaimed.pk}/", {"email": "x@y.org"}, format="json").status_code == 403

    def test_creator_org_edits_unclaimed_including_email(self, profiles):
        world, unclaimed, *_ = profiles
        c = api(world.staff_user)
        resp = c.patch(f"/api/v1/speakers/{unclaimed.pk}/", {"email": "una@new.example", "bio": "B"}, format="json")
        assert resp.status_code == 200
        unclaimed.refresh_from_db()
        assert unclaimed.email == "una@new.example"

    def test_claimed_profile_belongs_to_the_speaker(self, profiles):
        world, _, claimed, owner, _ = profiles
        sharing.grant(claimed, world.org, SpeakerShareGrant.Source.PITCH)
        assert (
            api(world.staff_user).patch(f"/api/v1/speakers/{claimed.pk}/", {"bio": "X"}, format="json").status_code
            == 403
        )
        # Speakers edit their own profile through /speakers/me/ (the portal uses the same model).
        assert api(owner).patch("/api/v1/speakers/me/", {"bio": "Mine"}, format="json").status_code == 200
        claimed.refresh_from_db()
        assert claimed.bio == "Mine"

    def test_admin_follows_the_same_rules(self, client, profiles):
        world, unclaimed, claimed, _, other_staff = profiles
        other_staff.is_staff = True
        other_staff.save()
        client.force_login(other_staff)
        page = client.get(reverse("admin:speakers_speakerprofile_change", args=[claimed.pk])).content.decode()
        assert 'name="bio"' not in page  # read-only view
        client.force_login(world.staff_user)
        world.staff_user.is_staff = True
        world.staff_user.save()
        page = client.get(reverse("admin:speakers_speakerprofile_change", args=[unclaimed.pk])).content.decode()
        assert 'name="bio"' in page and 'name="email"' in page
