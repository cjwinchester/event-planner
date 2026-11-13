"""Adapter: Event Bundle → the existing conference-schedule-ui JSON format.

Lets today's schedule UI (github.com/ireapps conference-schedule-ui) render
events planned here with no UI changes, while the bundle stays canonical.
Retire this exporter once the UI reads the bundle directly.
Shape follows that repo's schema/schedule.schema.json (copied into
apps/publishing/tests/fixtures/ and validated in tests).
"""

import json

from .base import Exporter, ExportFile, local, register, room_index, speaker_index, speaker_line, track_index


def _ui_time(value: str | None) -> str:
    # The UI requires millisecond UTC strings: 2026-03-05T15:00:00.000Z
    return value.replace("Z", ".000Z") if value else ""


@register
class ScheduleUIExporter(Exporter):
    slug = "schedule_ui"
    label = "conference-schedule-ui JSON (legacy adapter)"

    def filenames(self, bundle):
        return {"schedule_ui": f"{bundle['event']['slug']}-schedule.json"}

    def render(self, bundle, context):
        event, tz = bundle["event"], bundle["event"]["timezone"]
        rooms, tracks, speakers = room_index(bundle), track_index(bundle), speaker_index(bundle)
        sponsors_by_id = {s["id"]: s for s in bundle.get("sponsors", [])}
        venue = next((v for v in event.get("venues", []) if v.get("role") == "primary"), None)
        venue = venue or (event.get("venues") or [None])[0]
        addr = (venue or {}).get("address", {})
        street = ", ".join(p for p in (addr.get("address1"), addr.get("address2")) if p)
        city_line = " ".join(
            p for p in (addr.get("city", "") + ",", addr.get("region", ""), addr.get("postal_code", "")) if p.strip(",")
        )
        tz_short = local(bundle["generated_at"], tz).tzname() if tz else ""

        sessions = []
        for s in bundle["sessions"]:
            sessions.append(
                {
                    "session_id": s["id"],
                    "session_title": s["title"],
                    "description": s.get("description", ""),
                    "start_time": _ui_time(s.get("starts_at")),
                    "end_time": _ui_time(s.get("ends_at")),
                    "duration": s.get("duration_minutes") or 0,
                    "skill_level": s.get("skill_level", ""),
                    "session_type": s.get("type", ""),
                    "tracks": [tracks[t]["name"] for t in s.get("tracks", []) if t in tracks],
                    "room": rooms.get(s.get("room_id"), {}).get("name", ""),
                    "speaker_ids": [ss["speaker_id"] for ss in s.get("speakers", [])],
                    "speakers": speaker_line(s, speakers),
                    "sponsored_by": ", ".join(
                        sponsors_by_id[i]["name"] for i in s.get("sponsor_ids", []) if i in sponsors_by_id
                    ),
                    "cost": s.get("cost", ""),
                }
            )

        meta_events: dict[str, list[dict]] = {}
        for item in bundle.get("logistics", []):
            start = local(item["starts_at"], tz)
            meta_events.setdefault(start.date().isoformat(), []).append(
                {
                    "datetime_start": _ui_time(item["starts_at"]),
                    "datetime_end": _ui_time(item["ends_at"]),
                    "date": start.date().isoformat(),
                    "type": item["title"],
                    "room": item.get("location", ""),
                    "description": item.get("description", ""),
                }
            )

        floorplans = [
            {"floor": fp.get("level", fp["label"]), "image_url": fp["image_url"]}
            for v in event.get("venues", [])
            for fp in v.get("floorplans", [])
            if fp.get("image_url")
        ]

        payload = {
            "name": event["name"],
            "conference": event["name"],
            "slug": event["slug"],
            "description": event.get("description", ""),
            "splash_page_intro_text": event.get("intro_text", ""),
            "start_date": event["start_date"],
            "end_date": event["end_date"],
            "timezone": tz,
            "timezone_short": tz_short,
            "virtual": event.get("format") == "virtual",
            "updated": bundle["generated_at"],
            "banner_image": event.get("banner_image", ""),
            "registration_link": event.get("registration_url", ""),
            "url_registration": event.get("registration_url", ""),
            "url_conference_details": event.get("details_url", ""),
            "conf_city": addr.get("city", ""),
            "hotel_name": (venue or {}).get("name", ""),
            "hotel_address": " ".join(p for p in (street, city_line) if p),
            "hotel_gmaps_link": (venue or {}).get("maps_url", ""),
            "location": {
                "venue_name": (venue or {}).get("name", ""),
                "venue_address": street,
                "venue_city": addr.get("city", ""),
                "venue_state": addr.get("region", ""),
                "venue_postal_code": addr.get("postal_code", ""),
                "venue_gmaps_link": (venue or {}).get("maps_url", ""),
            },
            "schedule_formats": {
                k: v for k, v in context.feeds.items() if k in ("ui", "txt", "csv", "json", "pdf", "ics")
            },
            "sessions": sessions,
            "speakers": [
                {
                    "id": sp["id"],
                    "first_name": sp["first_name"],
                    "last_name": sp.get("last_name", ""),
                    "affiliation": sp.get("affiliation", ""),
                    "bio": sp.get("bio", ""),
                    "headshot": sp.get("headshot_url", ""),
                    "session_ids": sp.get("session_ids", []),
                    "social": [
                        {"label": link.get("label") or link.get("kind", ""), "url": link["url"]}
                        for link in sp.get("links", [])
                    ],
                }
                for sp in bundle["speakers"]
            ],
            "sponsors": [
                {
                    "id": sp["id"],
                    "name": sp["name"],
                    "description": sp.get("description", ""),
                    "logo_image_url": sp.get("logo_url", ""),
                    "sponsor_level": sp.get("level", ""),
                    "url": sp.get("url", ""),
                }
                for sp in bundle.get("sponsors", [])
                if sp.get("scope") == "event"
            ],
            "exhibitors": [
                {
                    "id": ex["id"],
                    "name": ex["name"],
                    "description": ex.get("description", ""),
                    "logo_image_url": ex.get("logo_url", ""),
                    "url": ex.get("url", ""),
                }
                for ex in bundle.get("exhibitors", [])
            ],
            "rooms": {
                r["name"]: {
                    "level": r.get("floor", ""),
                    "os": {"byo": "BYO", "pc": "PC", "mac": "OSX"}.get(r.get("computer_os", ""), ""),
                }
                for r in bundle["rooms"]
            },
            "tracks": {t["name"]: "" for t in bundle["tracks"]},
            "meta_events": meta_events,
            "additional_links": event.get("additional_links", []),
            "floorplans": floorplans,
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode()
        return [ExportFile(self.filenames(bundle)["schedule_ui"], body, "application/json")]
