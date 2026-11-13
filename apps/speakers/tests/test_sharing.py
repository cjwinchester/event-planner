import pytest

from apps.speakers import sharing
from apps.speakers.models import SpeakerShareGrant

Source = SpeakerShareGrant.Source


@pytest.mark.django_db
def test_private_fields_hidden_without_grant(world):
    public = sharing.visible_profile(world.alice, world.other_org)
    assert "email" not in public
    sharing.grant(world.alice, world.other_org, Source.PITCH)
    assert sharing.visible_profile(world.alice, world.other_org)["email"] == "alice@example.com"


@pytest.mark.django_db
def test_automatic_grant_does_not_override_speaker_revocation(world):
    sharing.grant(world.alice, world.org, Source.PITCH)
    sharing.revoke(world.alice, world.org, by_speaker=True)
    sharing.grant(world.alice, world.org, Source.ENGAGEMENT)
    assert not sharing.can_view_private(world.org, world.alice)
    # A new pitch is an explicit act by the speaker and re-grants.
    sharing.grant(world.alice, world.org, Source.PITCH)
    assert sharing.can_view_private(world.org, world.alice)


@pytest.mark.django_db
def test_self_view_always_includes_private_fields(world):
    assert sharing.visible_profile(world.alice, None, is_self=True)["email"] == "alice@example.com"
