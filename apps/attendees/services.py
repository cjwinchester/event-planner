"""Issuing passes and building what a pass page shows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.urls import reverse

from apps.events.models import Registration
from apps.program.models import Session, SessionSpeaker, join_url_for
from apps.publishing.cache import public_bundle
from apps.speakers import sharing

from .models import AttendeePass


@dataclass
class IssueResult:
    created: int = 0
    updated: int = 0


def issue_passes(event) -> IssueResult:
    """One pass per speaker on an active session, then one per active
    registration whose email isn't already covered. Idempotent: run it again
    after each registration sync. Speaker emails are filled in only where the
    speaker shares private details with this org (speakers.sharing)."""
    result = IssueResult()
    org = event.organization
    with transaction.atomic():
        speakers = {
            ss.speaker
            for ss in SessionSpeaker.objects.filter(session__event=event)
            .exclude(session__status="canceled")
            .select_related("speaker")
        }
        for sp in sorted(speakers, key=lambda x: x.full_name):
            email = sp.email if sharing.can_view_private(org, sp) else ""
            defaults = {"kind": AttendeePass.Kind.SPEAKER, "name": sp.full_name, "email": email}
            obj, created = AttendeePass.objects.get_or_create(event=event, speaker=sp, defaults=defaults)
            if not created and (obj.name, obj.email) != (sp.full_name, email or obj.email):
                obj.name, obj.email = sp.full_name, email or obj.email
                obj.save(update_fields=["name", "email", "updated_at"])
                result.updated += 1
            result.created += created
        covered = {e.lower() for e in AttendeePass.objects.filter(event=event).values_list("email", flat=True) if e}
        regs = Registration.objects.filter(event=event, canceled_at__isnull=True).exclude(email="")
        for reg in regs.order_by("last_name", "first_name"):
            if AttendeePass.objects.filter(event=event, registration=reg).exists():
                continue
            if reg.email.lower() in covered:
                # Same person as a speaker pass: link the registration so cancelation still applies.
                AttendeePass.objects.filter(event=event, email__iexact=reg.email, registration__isnull=True).update(
                    registration=reg
                )
                continue
            name = " ".join(p for p in (reg.first_name, reg.last_name) if p) or reg.email
            AttendeePass.objects.create(event=event, registration=reg, name=name, email=reg.email)
            covered.add(reg.email.lower())
            result.created += 1
    return result


def pass_url(p: AttendeePass, request=None) -> str:
    path = reverse("attendees:home", args=[p.token])
    if request is not None:
        return request.build_absolute_uri(path)
    base = settings.EVENT_PLANNER.get("SITE_URL", "").rstrip("/")
    return f"{base}{path}"


def device_hash(request) -> str:
    raw = f"{settings.SECRET_KEY}|{request.META.get('REMOTE_ADDR', '')}|{request.META.get('HTTP_USER_AGENT', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def pass_bundle(p: AttendeePass, request) -> dict:
    """The *public* bundle (so the page shows exactly what's published) with
    each session's live_url pointed at this pass's join redirect."""
    bundle = public_bundle(p.event)  # cached; a fresh copy we may personalize
    ids = [s["id"] for s in bundle["sessions"]]
    rows = Session.objects.filter(event=p.event, public_id__in=ids).select_related("room__room", "virtual_meeting")
    linked = {s.public_id for s in rows if join_url_for(s)}
    for s in bundle["sessions"]:
        has_link = s["id"] in linked and not s.get("canceled")
        url = reverse("attendees:join", args=[p.token, s["id"]])
        s["live_url"] = request.build_absolute_uri(url) if has_link else ""
    return bundle
