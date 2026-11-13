"""Import an Event Bundle (schemas/event-bundle/v1.json) into an organization.

This is the ONLY import format. Legacy data (SuperGrid sheets, old schedule
feeds, other vendors' exports) is transformed into a bundle upstream, then
loaded here — so there is one code path to maintain and test.

    import_bundle(bundle, organization, dry_run=False) -> ImportResult
    manage.py import_event path/to/bundle.json --org ire [--dry-run]

Matching (idempotent — re-importing updates rather than duplicates):
  event      (organization, event.slug)
  series     (organization, series.slug)
  venue      name + city (global directory; created if new)
  room       venue + name (case-insensitive)
  track      (event, slug)
  session    (event, public id)
  speaker    public id → email (if provided) → new profile. Existing global
             profiles only get *blank* fields filled — an import never
             overwrites what a speaker or another org entered.
  logistics  (event, title, starts_at)
  company    slug
Historical data may contain conflicts; imports don't block on them —
`ImportResult.conflicts` reports them instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from django.db import transaction
from django.db.models.functions import Lower
from django.utils.text import slugify

from apps.commerce.models import Company, DealStatus, ExhibitorBooking, Sponsorship, SponsorshipLevel
from apps.events.models import Event, EventRoom, EventSeries, EventVenue
from apps.program.conflicts import Severity, event_conflicts
from apps.program.models import (
    LogisticsCategory,
    LogisticsItem,
    Session,
    SessionSpeaker,
    SessionType,
    SkillLevel,
    Track,
)
from apps.speakers import sharing
from apps.speakers.models import EventSpeaker, SpeakerLink, SpeakerProfile, SpeakerShareGrant
from apps.venues.models import Room, Venue

from .bundle import validate_bundle


@dataclass
class ImportResult:
    event: Event | None = None
    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    conflicts: int = 0

    def bump(self, kind: str, created: bool) -> None:
        bucket = self.created if created else self.updated
        bucket[kind] = bucket.get(kind, 0) + 1

    def summary(self) -> str:
        made = ", ".join(f"{v} {k}" for k, v in sorted(self.created.items())) or "nothing"
        changed = ", ".join(f"{v} {k}" for k, v in sorted(self.updated.items())) or "nothing"
        return f"Created {made}; updated {changed}; {self.conflicts} schedule conflicts; {len(self.warnings)} warnings."


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class _Rollback(Exception):
    pass


def import_bundle(bundle: dict, organization, *, dry_run: bool = False) -> ImportResult:
    validate_bundle(bundle)
    result = ImportResult()
    try:
        with transaction.atomic():
            _import(bundle, organization, result)
            errors = [c for c in event_conflicts(result.event) if c.severity == Severity.ERROR]
            result.conflicts = len(errors)
            if dry_run:
                raise _Rollback
    except _Rollback:
        pass
    return result


def _import(bundle: dict, org, result: ImportResult) -> None:
    ev = bundle["event"]

    series = None
    if ev.get("series"):
        series, created = EventSeries.objects.get_or_create(
            organization=org, slug=ev["series"]["slug"], defaults={"name": ev["series"]["name"]}
        )
        result.bump("series", created)

    event, created = Event.objects.update_or_create(
        organization=org,
        slug=ev["slug"],
        defaults={
            "series": series,
            "name": ev["name"],
            "short_name": ev.get("short_name", ""),
            "kind": ev.get("kind", Event.Kind.CONFERENCE),
            "format": ev.get("format", Event.Format.IN_PERSON),
            "description": ev.get("description", ""),
            "intro_text": ev.get("intro_text", ""),
            "start_date": date.fromisoformat(ev["start_date"]),
            "end_date": date.fromisoformat(ev["end_date"]),
            "timezone": ev["timezone"],
            "registration_url": ev.get("registration_url", ""),
            "details_url": ev.get("details_url", ""),
            "additional_links": ev.get("additional_links", []),
        },
    )
    result.event = event
    result.bump("events", created)

    # ---- venues & rooms -------------------------------------------------
    venue_by_key: dict[str, Venue] = {}
    for i, v in enumerate(ev.get("venues", [])):
        addr = v.get("address", {})
        venue = Venue.objects.filter(name__iexact=v["name"], city__iexact=addr.get("city", "")).first()
        created = venue is None
        if created:
            venue = Venue.objects.create(
                name=v["name"],
                kind=v.get("kind", Venue.Kind.PHYSICAL),
                address1=addr.get("address1", ""),
                address2=addr.get("address2", ""),
                city=addr.get("city", ""),
                region=addr.get("region", ""),
                postal_code=addr.get("postal_code", ""),
                country=(addr.get("country") or "US")[:2],
                timezone=v.get("timezone", ""),
                website=v.get("website", ""),
                maps_url=v.get("maps_url", ""),
                public_access_notes=v.get("public_access_notes", ""),
                created_by_organization=org,
            )
            if v.get("location"):
                from django.contrib.gis.geos import Point

                venue.location = Point(v["location"]["lon"], v["location"]["lat"], srid=4326)
                venue.geocode_source = "import"
                venue.save(update_fields=["location", "geocode_source"])
        result.bump("venues", created)
        EventVenue.objects.get_or_create(
            event=event, venue=venue, defaults={"role": v.get("role", EventVenue.Role.PRIMARY), "sort_order": i}
        )
        venue_by_key[v["id"]] = venue
        if v.get("floorplans"):
            result.warnings.append(f"{venue.name}: floorplan images are not downloaded on import; upload them.")

    fallback_venue = next(iter(venue_by_key.values()), None)
    room_by_key: dict[str, EventRoom] = {}
    for r in bundle["rooms"]:
        venue = venue_by_key.get(r.get("venue_id") or "") or fallback_venue
        if venue is None:
            venue = Venue.objects.create(
                name=f"{event.name} venue",
                kind=Venue.Kind.VIRTUAL if event.format == Event.Format.VIRTUAL else Venue.Kind.PHYSICAL,
                created_by_organization=org,
            )
            EventVenue.objects.get_or_create(event=event, venue=venue)
            venue_by_key[""] = fallback_venue = venue
            result.warnings.append(f"No venue in bundle; created placeholder venue {venue.name!r}.")
        room = Room.objects.annotate(n=Lower("name")).filter(venue=venue, n=r["name"].lower()).first()
        if room is None:
            room = Room.objects.create(venue=venue, name=r["name"], floor_label=r.get("floor", ""))
            result.bump("rooms", True)
        event_room, created = EventRoom.objects.update_or_create(
            event=event,
            room=room,
            defaults={
                "sort_order": r.get("sort_order", 0),
                "setup": r.get("setup", ""),
                "capacity": r.get("capacity"),
                "computer_os": r.get("computer_os", ""),
                "is_recorded": r.get("is_recorded", False),
            },
        )
        result.bump("event rooms", created)
        room_by_key[r["id"]] = event_room

    # Combined rooms: only fill in parts for rooms that don't have any yet —
    # the shared venue directory may already know better.
    for r in bundle["rooms"]:
        parts = [room_by_key[p].room for p in r.get("parts", []) if p in room_by_key]
        combo = room_by_key[r["id"]].room
        if parts and not combo.parts.exists():
            combo.parts.set(parts)
            result.bump("combined rooms", True)

    # ---- tracks, lookups --------------------------------------------------
    track_by_slug = {}
    for i, t in enumerate(bundle["tracks"]):
        track, created = Track.objects.update_or_create(
            event=event,
            slug=t["id"],
            defaults={
                "name": t["name"],
                "description": t.get("description", ""),
                "color": t.get("color", ""),
                "sort_order": i,
            },
        )
        track_by_slug[t["id"]] = track
        result.bump("tracks", created)

    def session_type(name):
        return SessionType.objects.get_or_create(organization=org, name=name)[0] if name else None

    def skill_level(name):
        return SkillLevel.objects.get_or_create(organization=org, name=name)[0] if name else None

    # ---- speakers -----------------------------------------------------------
    speaker_by_key: dict[str, SpeakerProfile] = {}
    for sp in bundle["speakers"]:
        profile = SpeakerProfile.objects.filter(public_id=sp["id"]).first()
        if profile is None and sp.get("email"):
            profile = SpeakerProfile.objects.filter(email__iexact=sp["email"], merged_into__isnull=True).first()
        created = profile is None
        fields = {
            "first_name": sp["first_name"],
            "last_name": sp.get("last_name", ""),
            "pronouns": sp.get("pronouns", ""),
            "affiliation": sp.get("affiliation", ""),
            "job_title": sp.get("job_title", ""),
            "bio": sp.get("bio", ""),
            "headshot_url": sp.get("headshot_url", ""),
            "email": sp.get("email", ""),
        }
        if created:
            profile = SpeakerProfile.objects.create(public_id=sp["id"], created_by_organization=org, **fields)
            sharing.grant(profile, org, SpeakerShareGrant.Source.CREATED)
        else:
            blanks = {k: v for k, v in fields.items() if v and not getattr(profile, k)}
            if blanks:
                SpeakerProfile.objects.filter(pk=profile.pk).update(**blanks)
        existing_urls = set(profile.links.values_list("url", flat=True))
        for i, link in enumerate(sp.get("links", [])):
            if link["url"] not in existing_urls:
                kind = link.get("kind") if link.get("kind") in SpeakerLink.Kind.values else SpeakerLink.Kind.OTHER
                SpeakerLink.objects.create(
                    speaker=profile, kind=kind, url=link["url"], label=link.get("label", ""), sort_order=i
                )
        if sp.get("featured"):
            EventSpeaker.objects.update_or_create(event=event, speaker=profile, defaults={"is_featured": True})
        speaker_by_key[sp["id"]] = profile
        result.bump("speakers", created)

    # ---- sessions -----------------------------------------------------------
    session_by_public_id = {}
    for s in bundle["sessions"]:
        status = Session.Status.CANCELED if s.get("canceled") else s.get("status") or Session.Status.CONFIRMED
        session = Session.objects.filter(event=event, public_id=s["id"]).first()
        created = session is None
        if created:
            session = Session(event=event, public_id=s["id"])
        session.title = s["title"]
        session.summary = s.get("summary", "")
        session.description = s.get("description", "")
        session.title_status = s.get("title_status", Session.EditStatus.EDITED)
        session.description_status = s.get("description_status", Session.EditStatus.EDITED)
        session.session_type = session_type(s.get("type"))
        session.skill_level = skill_level(s.get("skill_level"))
        session.room = room_by_key.get(s.get("room_id") or "")
        session.starts_at, session.ends_at = _dt(s.get("starts_at")), _dt(s.get("ends_at"))
        session.is_plenary = s.get("is_plenary", False)
        session.is_recorded = s.get("is_recorded")
        session.requires_preregistration = s.get("requires_preregistration", False)
        session.capacity = s.get("capacity")
        session.cost = s.get("cost", "")
        session.live_url = s.get("live_url", "")
        session.recording_url = s.get("recording_url", "")
        session.status = status
        session.source = s.get("source", Session.Source.IMPORT)
        session.is_stock = s.get("is_stock", False)
        session.save()
        session.tracks.set([track_by_slug[t] for t in s.get("tracks", []) if t in track_by_slug])
        session.session_speakers.all().delete()
        for i, ss in enumerate(s.get("speakers", [])):
            profile = speaker_by_key.get(ss["speaker_id"])
            if profile is None:
                result.warnings.append(f"Session {s['id']}: unknown speaker {ss['speaker_id']}.")
                continue
            SessionSpeaker.objects.get_or_create(
                session=session,
                speaker=profile,
                role=ss.get("role", SessionSpeaker.Role.SPEAKER),
                defaults={"sort_order": i, "affiliation_override": ss.get("affiliation", "")},
            )
            EventSpeaker.objects.get_or_create(
                event=event, speaker=profile, defaults={"status": EventSpeaker.Status.CONFIRMED}
            )
        session_by_public_id[s["id"]] = session
        result.bump("sessions", created)

    # ---- logistics ---------------------------------------------------------
    for item in bundle.get("logistics", []):
        category, _ = LogisticsCategory.objects.get_or_create(
            organization=org, name=item.get("category") or "General", defaults={"public_by_default": True}
        )
        room = room_by_key.get(item.get("room_id") or "")
        _, created = LogisticsItem.objects.update_or_create(
            event=event,
            title=item["title"],
            starts_at=_dt(item["starts_at"]),
            defaults={
                "category": category,
                "description": item.get("description", ""),
                "room": room,
                "location_note": "" if room else item.get("location", ""),
                "ends_at": _dt(item["ends_at"]),
                "is_public": True,
                "blocks_room": False,  # historical data: don't let old logistics block sessions
            },
        )
        result.bump("logistics", created)

    # ---- sponsors & exhibitors --------------------------------------------
    for sp in bundle.get("sponsors", []):
        company = _company(sp, org)
        level = None
        if sp.get("level"):
            level, _ = SponsorshipLevel.objects.get_or_create(
                event=event, name=sp["level"], defaults={"rank": sp.get("level_rank") or 0}
            )
        scope = sp.get("scope", Sponsorship.Scope.EVENT)
        _, created = Sponsorship.objects.update_or_create(
            event=event,
            company=company,
            scope=scope,
            track=track_by_slug.get(sp.get("track_id") or ""),
            session=session_by_public_id.get(sp.get("session_id")),
            defaults={"level": level, "status": DealStatus.CONTRACTED, "is_public": True},
        )
        result.bump("sponsorships", created)
    for ex in bundle.get("exhibitors", []):
        _, created = ExhibitorBooking.objects.update_or_create(
            event=event,
            company=_company(ex, org),
            defaults={"booth_number": ex.get("booth", ""), "status": DealStatus.CONTRACTED, "is_public": True},
        )
        result.bump("exhibitors", created)


def _company(data: dict, org) -> Company:
    slug = slugify(data["id"]) or slugify(data["name"])
    company, _ = Company.objects.get_or_create(
        slug=slug,
        defaults={
            "name": data["name"],
            "description": data.get("description", ""),
            "website": data.get("url", ""),
            "logo_url": data.get("logo_url", ""),
            "created_by_organization": org,
        },
    )
    return company
