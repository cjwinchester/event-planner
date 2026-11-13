"""ICS calendar feeds with stable URLs and UIDs.

Files: <event>.ics (every published session) and ics/track-<slug>.ics per
track. UIDs are derived from the session's stable public ID, so calendar
apps update events in place when times or rooms change. Times are written
in UTC (unambiguous across DST); X-WR-TIMEZONE tells clients the event zone.
"""

from icalendar import Calendar, Event

from .base import Exporter, ExportFile, parse_utc, register, room_index, speaker_index, speaker_line
from .text import strip_html


def session_uid(bundle: dict, session_id: int, host: str) -> str:
    return f"{bundle['organization']['slug']}-{bundle['event']['slug']}-{session_id}@{host}"


def build_calendar(bundle: dict, sessions: list[dict], name: str, host: str, base_url: str = "") -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//event-planner//Event Bundle v1//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", name)
    cal.add("x-wr-timezone", bundle["event"]["timezone"])
    generated = parse_utc(bundle["generated_at"])
    rooms, speakers = room_index(bundle), speaker_index(bundle)
    for s in sessions:
        start, end = parse_utc(s.get("starts_at")), parse_utc(s.get("ends_at"))
        if not (start and end):
            continue
        ev = Event()
        ev.add("uid", session_uid(bundle, s["id"], host))
        ev.add("summary", s["title"])
        ev.add("dtstart", start)
        ev.add("dtend", end)
        ev.add("dtstamp", generated)
        if s.get("updated_at"):
            ev.add("last-modified", parse_utc(s["updated_at"]))
        room = rooms.get(s.get("room_id"))
        if room:
            ev.add("location", room["name"])
        parts = [strip_html(s.get("description", ""))]
        who = speaker_line(s, speakers)
        if who:
            parts.append(f"Speakers: {who}")
        if s.get("live_url"):
            parts.append(f"Join: {s['live_url']}")
        ev.add("description", "\n\n".join(p for p in parts if p))
        if s.get("live_url"):
            ev.add("url", s["live_url"])
        if s.get("canceled"):
            ev.add("status", "CANCELLED")
        cal.add_component(ev)
    return cal.to_ical()


@register
class ICSExporter(Exporter):
    slug = "ics"
    label = "Calendar feeds (ICS)"

    def filenames(self, bundle):
        return {"ics": f"{bundle['event']['slug']}.ics"}

    def render(self, bundle, context):
        event = bundle["event"]
        files = [
            ExportFile(
                self.filenames(bundle)["ics"],
                build_calendar(bundle, bundle["sessions"], event["name"], context.calendar_host),
                "text/calendar; charset=utf-8",
            )
        ]
        for track in bundle["tracks"]:
            in_track = [s for s in bundle["sessions"] if track["id"] in s.get("tracks", [])]
            if in_track:
                files.append(
                    ExportFile(
                        f"ics/track-{track['id']}.ics",
                        build_calendar(bundle, in_track, f"{event['name']}: {track['name']}", context.calendar_host),
                        "text/calendar; charset=utf-8",
                    )
                )
        return files
