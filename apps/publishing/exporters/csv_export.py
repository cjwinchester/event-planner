import csv
import io

from .base import Exporter, ExportFile, local, register, room_index, speaker_index, speaker_line, track_index


@register
class CSVExporter(Exporter):
    """One row per session (spreadsheet-friendly, local times)."""

    slug = "csv"
    label = "CSV"
    COLUMNS = [
        "session_id", "title", "type", "skill_level", "date", "start_time", "end_time", "duration_minutes",
        "room", "tracks", "speakers", "description",
    ]  # fmt: skip

    def filenames(self, bundle):
        return {"csv": f"{bundle['event']['slug']}-schedule.csv"}

    def render(self, bundle, context):
        tz = bundle["event"]["timezone"]
        rooms, tracks, speakers = room_index(bundle), track_index(bundle), speaker_index(bundle)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=self.COLUMNS)
        writer.writeheader()
        for s in bundle["sessions"]:
            start, end = local(s.get("starts_at"), tz), local(s.get("ends_at"), tz)
            writer.writerow(
                {
                    "session_id": s["id"],
                    "title": s["title"],
                    "type": s.get("type", ""),
                    "skill_level": s.get("skill_level", ""),
                    "date": start.date().isoformat() if start else "",
                    "start_time": start.strftime("%H:%M") if start else "",
                    "end_time": end.strftime("%H:%M") if end else "",
                    "duration_minutes": s.get("duration_minutes") or "",
                    "room": rooms.get(s.get("room_id"), {}).get("name", ""),
                    "tracks": ", ".join(tracks[t]["name"] for t in s.get("tracks", []) if t in tracks),
                    "speakers": speaker_line(s, speakers),
                    "description": s.get("description", ""),
                }
            )
        return [ExportFile(self.filenames(bundle)["csv"], buf.getvalue().encode(), "text/csv; charset=utf-8")]
