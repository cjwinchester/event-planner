"""Printable schedule: HTML template → PDF via WeasyPrint.

Also exposes `render_print_html()` so the same layout can be served as a
print-friendly web page. WeasyPrint needs Pango system libraries (present in
the Dockerfile; `brew install pango` on macOS).
"""

from django.template.loader import render_to_string

from .base import Exporter, ExportFile, local, register, room_index, sessions_by_day, speaker_index, speaker_line


def render_print_html(bundle: dict, include_descriptions: bool = True) -> str:
    tz = bundle["event"]["timezone"]
    rooms, speakers = room_index(bundle), speaker_index(bundle)
    days = []
    for label, sessions in sessions_by_day(bundle):
        rows = []
        for s in sessions:
            start, end = local(s.get("starts_at"), tz), local(s.get("ends_at"), tz)
            rows.append(
                {
                    "when": f"{start:%-I:%M}–{end:%-I:%M %p}" if start and end else "TBA",
                    "title": s["title"],
                    "room": rooms.get(s.get("room_id"), {}).get("name", ""),
                    "speakers": speaker_line(s, speakers),
                    "description": s.get("description", ""),  # already sanitized in the bundle
                }
            )
        days.append({"label": label, "sessions": rows})
    return render_to_string(
        "publishing/print_schedule.html",
        {"event": bundle["event"], "days": days, "include_descriptions": include_descriptions},
    )


@register
class PDFExporter(Exporter):
    slug = "pdf"
    label = "Printable PDF"

    def filenames(self, bundle):
        return {"pdf": f"{bundle['event']['slug']}-schedule.pdf"}

    def render(self, bundle, context):
        from weasyprint import HTML  # imported lazily: heavy, needs system libs

        pdf = HTML(string=render_print_html(bundle)).write_pdf()
        return [ExportFile(self.filenames(bundle)["pdf"], pdf, "application/pdf")]
