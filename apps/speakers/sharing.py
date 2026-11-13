"""Speaker-controlled sharing of private profile fields.

    grant(speaker, org, source)   — record consent (respecting revocations)
    revoke(speaker, org)          — speaker withdraws consent
    can_view_private(org, speaker)
    visible_profile(speaker, org) — dict of fields `org` may see

Policy (agreed with the project owner):
  * Pitching to an organization grants it by default.
  * The speaker can grant/revoke any organization from their portal.
  * Automatic grants (ENGAGEMENT, CREATED) never re-enable a grant the
    speaker revoked. A new pitch (explicit speaker action) and a direct
    SPEAKER grant do.
"""

from __future__ import annotations

from django.utils import timezone

from .models import SpeakerProfile, SpeakerShareGrant

Source = SpeakerShareGrant.Source
_EXPLICIT_SOURCES = {Source.PITCH, Source.SPEAKER}


def grant(speaker: SpeakerProfile, organization, source: str) -> SpeakerShareGrant:
    obj, created = SpeakerShareGrant.objects.get_or_create(
        speaker=speaker, organization=organization, defaults={"source": source}
    )
    if created or obj.is_active:
        return obj
    if source in _EXPLICIT_SOURCES or not obj.revoked_by_speaker:
        obj.revoked_at = None
        obj.revoked_by_speaker = False
        obj.source = source
        obj.granted_at = timezone.now()
        obj.save(update_fields=["revoked_at", "revoked_by_speaker", "source", "granted_at", "updated_at"])
    return obj


def revoke(speaker: SpeakerProfile, organization, *, by_speaker: bool = True) -> None:
    SpeakerShareGrant.objects.filter(speaker=speaker, organization=organization, revoked_at__isnull=True).update(
        revoked_at=timezone.now(), revoked_by_speaker=by_speaker, updated_at=timezone.now()
    )


def can_view_private(organization, speaker: SpeakerProfile) -> bool:
    if organization is None:
        return False
    return SpeakerShareGrant.objects.filter(
        speaker=speaker, organization=organization, revoked_at__isnull=True
    ).exists()


def granted_organization_ids(speaker: SpeakerProfile) -> set[int]:
    return set(
        SpeakerShareGrant.objects.filter(speaker=speaker, revoked_at__isnull=True).values_list(
            "organization_id", flat=True
        )
    )


def visible_profile(speaker: SpeakerProfile, organization=None, *, is_self: bool = False) -> dict:
    """Profile fields visible to `organization` (None = anonymous/public)."""
    data = {
        "public_id": speaker.public_id,
        "first_name": speaker.first_name,
        "last_name": speaker.last_name,
        "pronouns": speaker.pronouns,
        "affiliation": speaker.affiliation,
        "job_title": speaker.job_title,
        "bio": speaker.bio,
        "headshot": speaker.headshot_href,
        "links": [{"kind": link.kind, "label": link.display_label, "url": link.url} for link in speaker.links.all()],
    }
    if is_self or can_view_private(organization, speaker):
        data.update({f: getattr(speaker, f) for f in SpeakerProfile.PRIVATE_FIELDS})
    return data
