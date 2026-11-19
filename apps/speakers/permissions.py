"""Who may change a speaker profile (the profile itself is a shared directory).

Speaker profiles are global, so without these rules staff at any
organization could rewrite anyone's profile; and by changing an unclaimed
profile's email to their own, then logging in with it, take the profile over
(speakers.merging.claim_profiles links unclaimed profiles by verified email).

  * A claimed profile (`user` set) belongs to the speaker: only they (and
    superusers) change it. Organizations keep their own per-event copy of
    bio/affiliation on EventSpeaker.
  * An unclaimed profile's public fields may be changed by staff of an
    organization that created it or that the speaker shares details with.
  * Private fields (email, phone, location) of an unclaimed profile may be
    changed only by staff of the organization that created it, because the
    email decides who can claim the profile.
"""

from __future__ import annotations

from apps.organizations.models import Membership
from apps.organizations.permissions import organization_ids_for

from .models import SpeakerProfile, SpeakerShareGrant


def _staff_org_ids(user) -> set[int]:
    return set(organization_ids_for(user, Membership.Role.STAFF))


def can_edit_profile(user, profile: SpeakerProfile) -> bool:
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser or (profile.user_id and profile.user_id == user.pk):
        return True
    if profile.user_id:
        return False
    orgs = _staff_org_ids(user)
    if profile.created_by_organization_id in orgs:
        return True
    return SpeakerShareGrant.objects.filter(speaker=profile, organization_id__in=orgs, revoked_at__isnull=True).exists()


def can_edit_private(user, profile: SpeakerProfile | None) -> bool:
    if not (user and user.is_authenticated):
        return False
    if profile is None or profile.pk is None:  # creating: the creating org enters contact info
        return bool(user.is_superuser or _staff_org_ids(user))
    if user.is_superuser or (profile.user_id and profile.user_id == user.pk):
        return True
    return profile.user_id is None and profile.created_by_organization_id in _staff_org_ids(user)
