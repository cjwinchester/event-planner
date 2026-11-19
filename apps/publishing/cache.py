"""Cached public bundle (and rendered feeds) per event.

The public feeds and every attendee's pass page/calendar used to rebuild
the bundle on each request; with hundreds of attendees opening their pages
minutes before a session, that is the hottest path in the app. The bundle
is cached in the shared cache under a cheap *content version*: a handful of
aggregate queries that change whenever the schedule, speaker line-ups,
confirmations, rooms, tracks, logistics, sponsorships or the event itself
change. Edits the version doesn't see (a speaker's bio, a company logo)
appear within TTL seconds.

Callers get a deep copy, since some (pass pages) personalize it.
"""

from __future__ import annotations

import copy
import hashlib

from django.core.cache import cache
from django.db.models import Count, Max, Q

from .bundle import Audience, build_event_bundle

TTL = 300


def content_version(event) -> str:
    from apps.commerce.models import ExhibitorBooking, Sponsorship
    from apps.events.models import EventRoom
    from apps.program.models import LogisticsItem, Session, SessionSpeaker, Track
    from apps.speakers.models import EventSpeaker

    parts = [event.updated_at.isoformat()]
    for qs in (
        Session.objects.filter(event=event),
        LogisticsItem.objects.filter(event=event),
        Sponsorship.objects.filter(event=event),
        ExhibitorBooking.objects.filter(event=event),
    ):
        agg = qs.aggregate(n=Count("pk"), m=Max("updated_at"))
        parts.append(f"{agg['n']}:{agg['m']}")
    ss = SessionSpeaker.objects.filter(session__event=event).aggregate(
        n=Count("pk"), c=Count("pk", filter=Q(is_confirmed=True)), m=Max("pk"), o=Max("sort_order")
    )
    parts.append(f"{ss['n']}:{ss['c']}:{ss['m']}:{ss['o']}")
    es = EventSpeaker.objects.filter(event=event).aggregate(n=Count("pk"), m=Max("updated_at"))
    parts.append(f"{es['n']}:{es['m']}")
    parts.append(repr(list(EventRoom.objects.filter(event=event).values_list("pk", "sort_order", "is_public"))))
    parts.append(repr(list(Track.objects.filter(event=event).values_list("pk", "name", "color"))))
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def _key(event, what: str) -> str:
    return f"pub:{event.pk}:{content_version(event)}:{what}"


def public_bundle(event) -> dict:
    key = _key(event, "bundle")
    bundle = cache.get(key)
    if bundle is None:
        bundle = build_event_bundle(event, Audience.PUBLIC)
        cache.set(key, bundle, TTL)
    return copy.deepcopy(bundle)


def cached_render(event, what: str, render) -> bytes:
    """Cache an expensive rendering of the public bundle (PDF, CSV...)."""
    key = _key(event, f"render:{what}")
    body = cache.get(key)
    if body is None:
        body = render(public_bundle(event))
        cache.set(key, body, TTL)
    return body
