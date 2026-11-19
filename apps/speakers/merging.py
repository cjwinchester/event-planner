"""Merge duplicate speaker profiles into one.

Imports create one profile per person per conference (the rescue files give
the same person a different ID each year), so a speaker who logs in may
match several unclaimed profiles by email. `merge_profiles(keep, others)`
moves everything that points at `others` onto `keep` and marks the others
`merged_into=keep` (kept, not deleted, so old IDs can still be resolved).

Rules:
  * Profile fields: `keep`'s values win (callers pass the most recently
    updated profile as `keep`); blanks on `keep` are filled from the others.
  * Rows that would collide on a unique constraint are de-duplicated:
    the same speaker twice on one session/pitch/event keeps `keep`'s row.
  * Sharing grants, per organization: a revocation by the speaker always
    wins (privacy first); otherwise an active grant wins.
  * `public_id` of `keep` is unchanged. Others' public IDs disappear from
    feeds on the next publish (they were duplicates of the same person).
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models.functions import Lower

from .models import SpeakerProfile, SpeakerShareGrant

FILLABLE = [
    "first_name", "last_name", "pronouns", "affiliation", "job_title", "bio", "headshot", "headshot_url",
    "email", "phone", "city", "region", "country", "created_by_organization",
]  # fmt: skip


def _fill_blanks(keep: SpeakerProfile, others: list[SpeakerProfile]) -> None:
    changed = []
    for f in FILLABLE:
        if getattr(keep, f):
            continue
        for o in others:
            value = getattr(o, f)
            if value:
                setattr(keep, f, value)
                changed.append(f)
                break
    if changed:
        keep.save(update_fields=[*changed, "updated_at"])


def _merge_grants(keep: SpeakerProfile, other: SpeakerProfile) -> None:
    for g in SpeakerShareGrant.objects.filter(speaker=other):
        mine = SpeakerShareGrant.objects.filter(speaker=keep, organization_id=g.organization_id).first()
        if mine is None:
            g.speaker = keep
            g.save(update_fields=["speaker", "updated_at"])
            continue
        if g.revoked_by_speaker and not mine.revoked_by_speaker:
            mine.revoked_at, mine.revoked_by_speaker = g.revoked_at, True
            mine.save(update_fields=["revoked_at", "revoked_by_speaker", "updated_at"])
        elif mine.revoked_at and not mine.revoked_by_speaker and g.is_active:
            mine.revoked_at, mine.source, mine.granted_at = None, g.source, g.granted_at
            mine.save(update_fields=["revoked_at", "source", "granted_at", "updated_at"])
        g.delete()


def _repoint(keep: SpeakerProfile, other: SpeakerProfile) -> None:
    """Move every FK/M2M that points at `other` to `keep`, dropping collisions."""
    for rel in SpeakerProfile._meta.related_objects:
        model, field = rel.related_model, rel.field
        if model is SpeakerShareGrant:
            continue  # handled by _merge_grants
        if rel.many_to_many:
            for obj in model.objects.filter(**{field.name: other}):
                getattr(obj, field.name).remove(other)
                getattr(obj, field.name).add(keep)
            continue
        for obj in model.objects.filter(**{field.name: other}):
            setattr(obj, field.name, keep)
            try:
                with transaction.atomic():
                    obj.save(update_fields=[field.name])
            except IntegrityError:
                _absorb(model, obj, field, keep, other)


def _absorb(model, obj, field, keep, other) -> None:
    """`keep` already has the equivalent row (same session/event/role...).

    Nullable links (an attendee pass) are just unlinked, so a link someone
    already received keeps working. Otherwise the duplicate's non-blank
    values fill blanks on `keep`'s row (an org's notes, form answers) and
    the duplicate is deleted."""
    if field.null:
        setattr(obj, field.name, None)
        obj.save(update_fields=[field.name])
        return
    setattr(obj, field.name, other)  # restore for the lookup below
    unique = next((c.fields for c in model._meta.constraints if field.name in getattr(c, "fields", ())), None)
    if unique is None:
        obj.delete()
        return
    lookup = {f: (keep if f == field.name else getattr(obj, f)) for f in unique}
    existing = model.objects.filter(**lookup).first()
    if existing is not None:
        filled = []
        for f in model._meta.concrete_fields:
            if f.primary_key or f.name in unique or f.name in ("created_at", "updated_at"):
                continue
            if not getattr(existing, f.attname) and getattr(obj, f.attname):
                setattr(existing, f.attname, getattr(obj, f.attname))
                filled.append(f.attname)
        if filled:
            existing.save(update_fields=filled)
    obj.delete()


def merge_profiles(keep: SpeakerProfile, others) -> SpeakerProfile:
    others = [o for o in others if o.pk != keep.pk]
    with transaction.atomic():
        _fill_blanks(keep, others)
        for other in others:
            _merge_grants(keep, other)
            _repoint(keep, other)
            other.merged_into = keep
            other.user = None
            other.save(update_fields=["merged_into", "user", "updated_at"])
    return keep


def claim_profiles(user) -> SpeakerProfile | None:
    """Link `user` to their speaker profile by verified email, merging duplicates.

    Already-linked users keep their profile; unclaimed profiles with the same
    email are merged into it. Otherwise the most recently updated unclaimed
    match becomes theirs. Only verified addresses count (allauth), so nobody
    can claim a speaker by typing their email.
    """
    from allauth.account.models import EmailAddress

    emails = {e.lower() for e in EmailAddress.objects.filter(user=user, verified=True).values_list("email", flat=True)}
    if not emails:
        return SpeakerProfile.objects.filter(user=user).first()
    matches = list(
        SpeakerProfile.objects.annotate(lower_email=Lower("email")).filter(
            user__isnull=True, merged_into__isnull=True, lower_email__in=emails
        )
    )
    mine = SpeakerProfile.objects.filter(user=user, merged_into__isnull=True).first()
    if mine is None and not matches:
        return None
    if mine is None:
        mine = max(matches, key=lambda p: p.updated_at)
        mine.user = user
        mine.save(update_fields=["user", "updated_at"])
    if matches:
        merge_profiles(mine, [p for p in matches if p.pk != mine.pk])
    return mine
