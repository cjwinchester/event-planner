#!/usr/bin/env python
"""Convert a conference-schedule-rescue schedule file into an Event Bundle.

This is an *upstream* converter: it lives next to the app but never touches
the database. It turns one legacy format into schemas/event-bundle/v1.json,
which `manage.py import_event` then loads. Use it as a template for
converting other legacy formats.

    uv run python tools/converters/rescue_to_bundle.py \\
        ~/conference-schedule-rescue/docs/data/nicar-2026-schedule.json \\
        --org-slug ire --org-name "Investigative Reporters & Editors" -o /tmp/nicar-2026.bundle.json
    uv run python manage.py import_event /tmp/nicar-2026.bundle.json --org ire --dry-run

Source format (conference-schedule-rescue docs/data/*-schedule.json):
  conference, slug, start_date, end_date, timezone, conference_series,
  location {venue_name, city, state},
  sessions [{session_id (8 hex), start_time/end_time (naive, event-local),
             room, session_title, session_type, speaker_ids, speakers
             ("Name, Affiliation; ..."), description (plain text)}],
  speakers [{id (8 hex), first_name, last_name, name, affiliation, bio}]

Mapping decisions:
  * Session ids: the bundle needs integers. The 8-hex id becomes
    int(hex) mod 2,147,483,647 (the DB column's max) — stable across
    re-runs even if sessions are added or removed (a sequence number
    wouldn't be). Collisions abort.
  * Times are event-local in the source; converted to UTC with zoneinfo,
    so DST changes mid-conference come out right.
  * Speaker ids are reused as-is, but they are per *file*: the rescue
    project gives the same person different ids in different conferences
    (20 people appear in both NICAR 2026 and AccessFest 2026 under different
    ids). Importing several files therefore creates duplicate profiles until
    they're merged (see docs/continuing.md, speaker dedupe).
  * A session's per-session affiliation (from the `speakers` string) is kept
    when it differs from the speaker's profile affiliation.
  * Combined rooms: "White River E-F" is linked to whichever of "White
    River E" / "White River F" the event uses (also "Salons 1-3" → 1, 2, 3).
  * Sessions with no room stay unscheduled (virtual events in this format
    have no rooms); the importer leaves them in the planner's pool.
  * Titles starting "CANCELLED -" become canceled sessions (prefix removed).
  * Rooms are listed in natural order (102, 104, 201 … White River B, E).
  * Plain-text descriptions become <p> paragraphs; tracks aren't in this
    format, so none are produced.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "event-bundle" / "v1.json"


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def to_utc(local: str, tz: ZoneInfo) -> str:
    return datetime.fromisoformat(local).replace(tzinfo=tz).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def paragraphs(text: str) -> str:
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text or "") if c.strip()]
    return "".join(f"<p>{html.escape(c).replace(chr(10), '<br>')}</p>" for c in chunks)


MAX_PUBLIC_ID = 2_147_483_647  # Session.public_id is a PositiveIntegerField


_CANCELED = re.compile(r"^\s*\(?cancell?ed\)?\s*[-–—:|]?\s*", re.IGNORECASE)


def natural_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def split_canceled(title: str) -> tuple[str, bool]:
    """'CANCELLED - Skills as …' → ('Skills as …', True). Legacy schedules
    mark cancellations in the title instead of a field."""
    stripped = _CANCELED.sub("", title, count=1)
    return (stripped.strip(), True) if stripped != title else (title.strip(), False)


def session_number(hex_id: str) -> int:
    return int(hex_id, 16) % MAX_PUBLIC_ID


def combined_parts(name: str, all_names: set[str]) -> list[str]:
    """'White River E-F' → ['White River E', 'White River F'];
    'Salons 1-3' → ['Salons 1', 'Salons 2', 'Salons 3'] — limited to the
    parts that exist in this event."""
    m = re.fullmatch(r"(.*?)(\w+)\s*-\s*(\w+)", name)
    if not m:
        return []
    prefix, a, b = m.groups()
    if a.isdigit() and b.isdigit() and int(a) < int(b):
        names = [f"{prefix}{n}" for n in range(int(a), int(b) + 1)]
    elif len(a) == len(b) == 1 and a.isalpha() and b.isalpha() and a < b:
        names = [f"{prefix}{chr(c)}" for c in range(ord(a), ord(b) + 1)]
    else:
        names = [f"{prefix}{a}", f"{prefix}{b}"]
    # Link whichever parts this event actually uses (NICAR 2026 used "White
    # River E" and "White River E-F" but never F alone) — enough for conflict
    # checks: booking E-F still blocks E.
    return [n for n in names if n in all_names]


def per_session_affiliations(speaker_string: str) -> dict[str, str]:
    """'Ann Lee, Daily News; Bo Kim, KXAN' → {'Ann Lee': 'Daily News', ...}."""
    out = {}
    for chunk in (speaker_string or "").split(";"):
        name, _, affiliation = chunk.strip().partition(",")
        if name.strip():
            out[name.strip()] = affiliation.strip()
    return out


def convert(src: dict, *, org_slug: str, org_name: str) -> tuple[dict, list[str]]:
    notes: list[str] = []
    tz = ZoneInfo(src["timezone"])
    location = src.get("location") or {}
    venue_name = location.get("venue_name") or f"{src['conference']} venue"
    is_virtual = "virtual" in venue_name.lower() or "zoom" in venue_name.lower()

    # Rooms in natural order (102, 104, 201 … White River B, E, E-F) — a
    # sensible default grid column order; staff can reorder per event.
    room_names = sorted({s["room"] for s in src["sessions"] if s.get("room")}, key=natural_key)
    room_id = {name: f"r-{slugify(name)}" for name in room_names}
    rooms = []
    for i, name in enumerate(room_names):
        room = {"id": room_id[name], "name": name, "venue_id": "v1", "sort_order": i}
        parts = combined_parts(name, set(room_names))
        if parts:
            room["parts"] = [room_id[p] for p in parts]
            notes.append(f"Combined room: {name} = {', '.join(parts)}")
        rooms.append(room)

    speakers_by_id = {sp["id"]: sp for sp in src.get("speakers", [])}
    session_ids_by_speaker: dict[str, list[int]] = {}
    sessions, seen_numbers = [], {}
    for s in src["sessions"]:
        number = session_number(s["session_id"])
        if number in seen_numbers:
            raise SystemExit(f"Session id collision: {s['session_id']} vs {seen_numbers[number]}")
        seen_numbers[number] = s["session_id"]
        printed = per_session_affiliations(s.get("speakers", ""))
        session_speakers = []
        for sid in s.get("speaker_ids", []):
            sp = speakers_by_id.get(sid)
            if sp is None:
                notes.append(f"Session {s['session_id']}: unknown speaker id {sid} skipped")
                continue
            entry = {"speaker_id": sid, "role": "speaker"}
            name = sp.get("name") or f"{sp.get('first_name', '')} {sp.get('last_name', '')}".strip()
            if printed.get(name) and printed[name] != (sp.get("affiliation") or ""):
                entry["affiliation"] = printed[name]
            session_speakers.append(entry)
            session_ids_by_speaker.setdefault(sid, []).append(number)
        has_time = bool(s.get("start_time") and s.get("end_time"))
        starts = to_utc(s["start_time"], tz) if has_time else None
        ends = to_utc(s["end_time"], tz) if has_time else None
        duration = None
        if has_time:
            delta = datetime.fromisoformat(ends.replace("Z", "+00:00")) - datetime.fromisoformat(
                starts.replace("Z", "+00:00")
            )
            duration = int(delta.total_seconds() // 60)
        title, canceled = split_canceled(s["session_title"])
        if canceled:
            notes.append(f"Canceled (from title): {title}")
        sessions.append(
            {
                "id": number,
                "title": title,
                "description": paragraphs(s.get("description", "")),
                "type": s.get("session_type", ""),
                "tracks": [],
                "room_id": room_id.get(s.get("room") or ""),
                "starts_at": starts,
                "ends_at": ends,
                "duration_minutes": duration,
                "speakers": session_speakers,
                "canceled": canceled,
                # Published historical sessions were edited before they went out.
                "title_status": "edited",
                "description_status": "edited",
                "status": "confirmed",
                "source": "import",
            }
        )
    unplaced = sum(1 for s in sessions if not s["room_id"])
    if unplaced:
        notes.append(f"{unplaced} sessions have no room and will import as unscheduled")

    speakers = []
    for sp in src.get("speakers", []):
        first = sp.get("first_name") or (sp.get("name") or "").split(" ")[0] or "?"
        speakers.append(
            {
                "id": sp["id"],
                "first_name": first,
                "last_name": sp.get("last_name") or "",
                "affiliation": sp.get("affiliation") or "",
                "bio": paragraphs(sp.get("bio") or ""),
                "links": [],
                "session_ids": session_ids_by_speaker.get(sp["id"], []),
            }
        )
    unused = [sp["id"] for sp in speakers if not sp["session_ids"]]
    if unused:
        notes.append(f"{len(unused)} speakers aren't on any session")

    series = src.get("conference_series")
    bundle = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "audience": "internal",
        "organization": {"slug": org_slug, "name": org_name},
        "event": {
            "slug": src["slug"],
            "name": src["conference"],
            "kind": "conference",
            "format": "virtual" if is_virtual else "in_person",
            "status": "completed" if src["end_date"] < datetime.now(UTC).date().isoformat() else "announced",
            "start_date": src["start_date"],
            "end_date": src["end_date"],
            "timezone": src["timezone"],
            "series": {"slug": slugify(series), "name": series} if series else None,
            "venues": [
                {
                    "id": "v1",
                    "role": "virtual" if is_virtual else "primary",
                    "name": venue_name,
                    "kind": "virtual" if is_virtual else "physical",
                    "address": {
                        "city": location.get("city") or "",
                        "region": location.get("state") or "",
                        "country": "US",
                    },
                }
            ],
        },
        "rooms": rooms,
        "tracks": [],
        "sessions": sessions,
        "speakers": speakers,
        "logistics": [],
        "sponsors": [],
        "exhibitors": [],
    }
    return bundle, notes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("--org-slug", required=True)
    parser.add_argument("--org-name", required=True)
    parser.add_argument("-o", "--output", type=Path, help="Write here (default: stdout)")
    args = parser.parse_args(argv)

    bundle, notes = convert(json.loads(args.source.read_text()), org_slug=args.org_slug, org_name=args.org_name)
    try:
        import jsonschema

        jsonschema.validate(bundle, json.loads(SCHEMA.read_text()))
    except ImportError:
        notes.append("jsonschema not installed; bundle not validated")
    text = json.dumps(bundle, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    print(
        f"{bundle['event']['name']}: {len(bundle['sessions'])} sessions, {len(bundle['speakers'])} speakers, "
        f"{len(bundle['rooms'])} rooms",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
