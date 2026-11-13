import html
import re
import textwrap

from .base import Exporter, ExportFile, local, register, room_index, sessions_by_day, speaker_index, speaker_line

_TAGS = re.compile(r"<[^>]+>")


def strip_html(value: str) -> str:
    text = re.sub(r"</p>|<br\s*/?>", "\n", value or "")
    return html.unescape(_TAGS.sub("", text)).strip()


@register
class TextExporter(Exporter):
    """Plain-text schedule (accessible, screen-reader and email friendly)."""

    slug = "txt"
    label = "Plain text"

    def filenames(self, bundle):
        return {"txt": f"{bundle['event']['slug']}-schedule.txt"}

    def render(self, bundle, context):
        event, tz = bundle["event"], bundle["event"]["timezone"]
        rooms, speakers = room_index(bundle), speaker_index(bundle)
        lines = [event["name"].upper(), f"{event['start_date']} to {event['end_date']} (times in {tz})", ""]
        for day, sessions in sessions_by_day(bundle):
            lines += ["=" * 72, day.upper(), "=" * 72, ""]
            for s in sessions:
                start, end = local(s.get("starts_at"), tz), local(s.get("ends_at"), tz)
                when = f"{start:%-I:%M %p}-{end:%-I:%M %p}" if start and end else "TBA"
                room = rooms.get(s.get("room_id"), {}).get("name", "")
                lines.append(f"{when}  {s['title']}")
                if room:
                    lines.append(f"  Room: {room}")
                who = speaker_line(s, speakers)
                if who:
                    lines.append(f"  Speakers: {who}")
                desc = strip_html(s.get("description", ""))
                if desc:
                    lines += textwrap.wrap(desc, 70, initial_indent="  ", subsequent_indent="  ")
                lines.append("")
        return [ExportFile(self.filenames(bundle)["txt"], "\n".join(lines).encode(), "text/plain; charset=utf-8")]
