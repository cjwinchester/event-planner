"""Build the canonical Event Bundle (schemas/event-bundle/v1.json).

The bundle is the single source of truth for everything downstream: the
API's bundle endpoint, every published file format (exporters render from
the bundle dict, never from the ORM), and the importer's input format.

Two audiences:
  PUBLIC   — only *ready* sessions (placed, timed, edited title, not
             canceled), only public logistics/sponsors/exhibitors, only
             public speaker fields, sanitized HTML. Nothing private, ever:
             no contracts, notes, emails, form answers, internal statuses.
  INTERNAL — every active session (scheduled or not) plus internal
             workflow fields; speaker emails only where the speaker has
             granted the organization access. For staff exports/backups.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import nh3
from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone

from apps.commerce.models import DealStatus, ExhibitorBooking, Sponsorship
from apps.program.models import LogisticsItem, Session, SessionSpeaker
from apps.speakers.models import EventSpeaker, SpeakerShareGrant
from apps.venues.models import Room

SCHEMA_VERSION = "1.0"
SCHEMA_PATH = Path(settings.BASE_DIR) / "schemas" / "event-bundle" / "v1.json"

_ALLOWED_TAGS = {
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "a",
    "ul",
    "ol",
    "li",
    "blockquote",
    "code",
    "pre",
    "h3",
    "h4",
}


class Audience(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"


@lru_cache(maxsize=1)
def bundle_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def validate_bundle(bundle: dict) -> None:
    """Raise jsonschema.ValidationError if `bundle` doesn't match v1."""
    import jsonschema

    jsonschema.validate(bundle, bundle_schema())


def sanitize_html(value: str) -> str:
    if not value:
        return ""
    return nh3.clean(value, tags=_ALLOWED_TAGS, attributes={"a": {"href", "title"}}, link_rel="noopener noreferrer")


def utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_url(field) -> str:
    try:
        return field.url if field else ""
    except ValueError:
        return ""


def room_key(event_room_id: int | None) -> str | None:
    return f"r{event_room_id}" if event_room_id else None


def build_event_bundle(event, audience: Audience = Audience.PUBLIC, *, feeds: dict | None = None) -> dict:
    """Assemble the bundle dict for `event`. Pure read; no side effects."""
    public = audience == Audience.PUBLIC
    org = event.organization

    # ---- sessions -------------------------------------------------------
    sessions_qs = Session.objects.filter(event=event).select_related("session_type", "skill_level", "room__room")
    sessions_qs = sessions_qs.ready() if public else sessions_qs.active()
    if public and not event.publish_schedule:
        sessions_qs = sessions_qs.none()
    sessions_qs = sessions_qs.prefetch_related(
        "tracks",
        "assets",
        Prefetch(
            "session_speakers",
            queryset=SessionSpeaker.objects.select_related("speaker").order_by("sort_order", "pk"),
        ),
    ).order_by("starts_at", "public_id")
    sessions = list(sessions_qs)

    sponsorships = []
    if not public or event.publish_sponsors:
        sponsorships = list(
            Sponsorship.objects.filter(event=event)
            .exclude(status__in=[DealStatus.PROSPECT, DealStatus.CANCELED])
            .filter(is_public=True)
            .select_related("company", "level", "track", "session")
        )
    sponsor_ids_by_session: dict[int, list[str]] = {}
    for sp in sponsorships:
        if sp.session_id:
            sponsor_ids_by_session.setdefault(sp.session_id, []).append(sp.company.slug)

    session_dicts = []
    speaker_session_ids: dict[int, list[int]] = {}
    speaker_objs = {}
    for s in sessions:
        public_assets = [a for a in s.assets.all() if a.is_public or not public]
        session_speakers = list(s.session_speakers.all())
        for ss in session_speakers:
            speaker_objs[ss.speaker_id] = ss.speaker
            speaker_session_ids.setdefault(ss.speaker_id, []).append(s.public_id)
        d = {
            "id": s.public_id,
            "title": s.title,
            "summary": s.summary,
            "description": sanitize_html(s.description),
            "type": s.session_type.name if s.session_type else "",
            "skill_level": s.skill_level.name if s.skill_level else "",
            "tracks": [t.slug for t in s.tracks.all()],
            "room_id": room_key(s.room_id),
            "starts_at": utc(s.starts_at),
            "ends_at": utc(s.ends_at),
            "duration_minutes": s.duration_minutes,
            "is_plenary": s.is_plenary,
            "is_recorded": s.effective_is_recorded,
            "requires_preregistration": s.requires_preregistration,
            "capacity": s.capacity,
            "cost": s.cost,
            "live_url": s.live_url,
            "recording_url": s.recording_url,
            "canceled": s.is_canceled,
            "speakers": [
                {
                    "speaker_id": ss.speaker.public_id,
                    "role": ss.role,
                    "affiliation": ss.affiliation_override or ss.speaker.affiliation,
                }
                for ss in session_speakers
            ],
            "sponsor_ids": sponsor_ids_by_session.get(s.pk, []),
            "assets": [{"kind": a.kind, "label": a.title, "url": _file_url(a.file) or a.url} for a in public_assets],
            "updated_at": utc(s.updated_at),
        }
        if not public:
            d.update(
                status=s.status,
                title_status=s.title_status,
                description_status=s.description_status,
                is_stock=s.is_stock,
                source=s.source,
            )
        session_dicts.append(d)

    # ---- speakers -------------------------------------------------------
    engagements = {
        e.speaker_id: e for e in EventSpeaker.objects.filter(event=event, speaker_id__in=speaker_objs.keys())
    }
    granted = set()
    if not public:
        granted = set(
            SpeakerShareGrant.objects.filter(
                organization=org, speaker_id__in=speaker_objs.keys(), revoked_at__isnull=True
            ).values_list("speaker_id", flat=True)
        )
    speaker_dicts = []
    if not public or event.publish_speakers:
        for sp in sorted(speaker_objs.values(), key=lambda p: (p.last_name.lower(), p.first_name.lower())):
            engagement = engagements.get(sp.pk)
            d = {
                "id": sp.public_id,
                "first_name": sp.first_name,
                "last_name": sp.last_name,
                "pronouns": sp.pronouns,
                "affiliation": engagement.display_affiliation if engagement else sp.affiliation,
                "job_title": sp.job_title,
                "bio": sanitize_html(engagement.display_bio if engagement else sp.bio),
                "headshot_url": sp.headshot_href,
                "featured": bool(engagement and engagement.is_featured),
                "links": [{"kind": link.kind, "label": link.display_label, "url": link.url} for link in sp.links.all()],
                "session_ids": speaker_session_ids.get(sp.pk, []),
            }
            if not public and sp.pk in granted and sp.email:
                d["email"] = sp.email
            speaker_dicts.append(d)

    # ---- rooms, tracks, venues -----------------------------------------
    event_rooms = list(event.event_rooms.select_related("room__floorplan").order_by("sort_order", "pk"))
    used_room_ids = {s.room_id for s in sessions}
    er_by_room = {er.room_id: er.pk for er in event_rooms}
    parts_by_room: dict[int, list[int]] = {}
    for combo_id, part_id in Room.parts.through.objects.filter(from_room_id__in=er_by_room).values_list(
        "from_room_id", "to_room_id"
    ):
        if part_id in er_by_room:
            parts_by_room.setdefault(combo_id, []).append(er_by_room[part_id])
    room_dicts = []
    for er in event_rooms:
        if public and (not er.is_public or (er.pk not in used_room_ids and not er.is_schedulable)):
            continue
        room = er.room
        has_pin = room.map_x is not None and room.map_y is not None
        room_dicts.append(
            {
                "id": room_key(er.pk),
                "name": er.name,
                "venue_id": f"v{room.venue_id}",
                "floor": room.floor,
                "floorplan_id": f"f{room.floorplan_id}" if room.floorplan_id else None,
                "map": {"x": room.map_x, "y": room.map_y} if has_pin else None,
                "capacity": er.effective_capacity,
                "setup": er.setup,
                "computer_os": er.computer_os,
                "is_recorded": er.is_recorded,
                "sort_order": er.sort_order,
                "parts": [room_key(pk) for pk in sorted(parts_by_room.get(room.pk, []))],
            }
        )

    track_dicts = [
        {"id": t.slug, "name": t.name, "description": t.description, "color": t.color}
        for t in event.tracks.order_by("sort_order", "name")
    ]

    venues = []
    for link in event.event_venues.select_related("venue").prefetch_related("venue__floorplans"):
        v = link.venue
        venues.append(
            {
                "id": f"v{v.pk}",
                "role": link.role,
                "name": v.name,
                "kind": v.kind,
                "address": {
                    "address1": v.address1,
                    "address2": v.address2,
                    "city": v.city,
                    "region": v.region,
                    "postal_code": v.postal_code,
                    "country": v.country,
                },
                "location": {"lat": v.location.y, "lon": v.location.x} if v.location else None,
                "timezone": v.timezone,
                "website": v.website,
                "maps_url": v.maps_url,
                "public_access_notes": v.public_access_notes,
                "floorplans": [
                    {
                        "id": f"f{fp.pk}",
                        "label": fp.label,
                        "level": fp.level,
                        "image_url": _file_url(fp.image),
                        "width": fp.width,
                        "height": fp.height,
                    }
                    for fp in v.floorplans.all()
                ],
            }
        )

    # ---- logistics, sponsors, exhibitors -------------------------------
    logistics = []
    if not public or event.publish_logistics:
        for item in (
            LogisticsItem.objects.filter(event=event, starts_at__isnull=False, ends_at__isnull=False)
            .select_related("category", "room__room")
            .order_by("starts_at")
        ):
            if public and not item.effective_is_public:
                continue
            logistics.append(
                {
                    "id": item.pk,
                    "category": item.category.name,
                    "title": item.title,
                    "description": item.description,
                    "room_id": room_key(item.room_id),
                    "location": item.room.name if item.room_id else item.location_note,
                    "starts_at": utc(item.starts_at),
                    "ends_at": utc(item.ends_at),
                }
            )

    sponsors = [
        {
            "id": sp.company.slug,
            "name": sp.display_name or sp.company.name,
            "description": sp.company.description,
            "url": sp.company.website,
            "logo_url": sp.company.logo_href,
            "level": sp.level.name if sp.level else "",
            "level_rank": sp.level.rank if sp.level else None,
            "scope": sp.scope,
            "track_id": sp.track.slug if sp.track_id else None,
            "session_id": sp.session.public_id if sp.session_id else None,
        }
        for sp in sponsorships
    ]

    exhibitors = []
    if not public or event.publish_exhibitors:
        for b in (
            ExhibitorBooking.objects.filter(event=event, is_public=True)
            .exclude(status__in=[DealStatus.PROSPECT, DealStatus.CANCELED])
            .select_related("company", "package")
            .order_by("company__name")
        ):
            exhibitors.append(
                {
                    "id": b.company.slug,
                    "name": b.company.name,
                    "description": b.company.description,
                    "url": b.company.website,
                    "logo_url": b.company.logo_href,
                    "booth": b.booth_number,
                    "package": b.package.name if b.package_id else "",
                }
            )

    bundle = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc(timezone.now()),
        "audience": str(audience),
        "organization": {"slug": org.slug, "name": org.name, "website": org.website},
        "event": {
            "slug": event.slug,
            "name": event.name,
            "short_name": event.short_name,
            "kind": event.kind,
            "format": event.format,
            "status": event.status,
            "description": sanitize_html(event.description),
            "intro_text": sanitize_html(event.intro_text),
            "start_date": event.start_date.isoformat(),
            "end_date": event.end_date.isoformat(),
            "timezone": event.timezone,
            "series": {"slug": event.series.slug, "name": event.series.name} if event.series_id else None,
            "registration_url": event.registration_url,
            "details_url": event.details_url,
            "banner_image": _file_url(event.banner_image),
            "additional_links": [
                {"label": link.get("label", ""), "url": link["url"]}
                for link in (event.additional_links or [])
                if isinstance(link, dict) and link.get("url")
            ],
            "venues": venues,
        },
        "rooms": room_dicts,
        "tracks": track_dicts,
        "sessions": session_dicts,
        "speakers": speaker_dicts,
        "logistics": logistics,
        "sponsors": sponsors,
        "exhibitors": exhibitors,
    }
    if feeds:
        bundle["feeds"] = dict(feeds)
    return bundle
