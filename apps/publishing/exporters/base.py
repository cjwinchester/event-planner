"""Exporter interface and registry.

An exporter turns an Event Bundle dict into one or more files. Exporters
must render only from the bundle (never the ORM) so every format agrees
with the canonical feed. Register new formats with `@register`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass
class ExportFile:
    path: str  # relative to the event's publish directory
    content: bytes
    content_type: str


@dataclass
class ExportContext:
    """What exporters may need beyond the bundle."""

    base_url: str = ""  # URL the event's publish directory is served from (trailing slash)
    feeds: dict[str, str] = field(default_factory=dict)  # format slug → public URL
    calendar_host: str = "event-planner"  # domain-ish suffix for ICS UIDs

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}" if self.base_url else path


class Exporter:
    slug: str = ""
    label: str = ""
    #: Included when publishing without an explicit format list.
    default: bool = True

    def filenames(self, bundle: dict) -> dict[str, str]:
        """Primary file path(s) this exporter writes, keyed by feed name —
        lets the publisher compute every feed URL before rendering."""
        return {}

    def render(self, bundle: dict, context: ExportContext) -> list[ExportFile]:
        raise NotImplementedError


_REGISTRY: dict[str, Exporter] = {}


def register(cls):
    instance = cls()
    if instance.slug in _REGISTRY:
        raise ValueError(f"Exporter {instance.slug!r} already registered")
    _REGISTRY[instance.slug] = instance
    return cls


def get_exporter(slug: str) -> Exporter:
    try:
        return _REGISTRY[slug]
    except KeyError:
        raise KeyError(f"No exporter {slug!r}. Available: {', '.join(sorted(_REGISTRY))}") from None


def all_exporters() -> dict[str, Exporter]:
    return dict(_REGISTRY)


def default_formats() -> list[str]:
    return [slug for slug, e in _REGISTRY.items() if e.default]


# ---- shared helpers -------------------------------------------------------


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def local(value: str | None, tz: str) -> datetime | None:
    dt = parse_utc(value)
    return dt.astimezone(ZoneInfo(tz)) if dt else None


def speaker_index(bundle: dict) -> dict[str, dict]:
    return {s["id"]: s for s in bundle.get("speakers", [])}


def room_index(bundle: dict) -> dict[str, dict]:
    return {r["id"]: r for r in bundle.get("rooms", [])}


def track_index(bundle: dict) -> dict[str, dict]:
    return {t["id"]: t for t in bundle.get("tracks", [])}


def speaker_line(session: dict, speakers: dict[str, dict]) -> str:
    """ "Name, Affiliation; Name, Affiliation" (the format existing feeds use)."""
    parts = []
    for ss in session.get("speakers", []):
        sp = speakers.get(ss["speaker_id"])
        if not sp:
            continue
        name = f"{sp['first_name']} {sp.get('last_name', '')}".strip()
        affiliation = ss.get("affiliation") or sp.get("affiliation", "")
        parts.append(f"{name}, {affiliation}" if affiliation else name)
    return "; ".join(parts)


def sessions_by_day(bundle: dict) -> list[tuple[str, list[dict]]]:
    tz = bundle["event"]["timezone"]
    days: dict[str, list[dict]] = {}
    for s in sorted(bundle["sessions"], key=lambda s: (s.get("starts_at") or "", s["id"])):
        start = local(s.get("starts_at"), tz)
        key = start.strftime("%A, %B %-d") if start else "Unscheduled"
        days.setdefault(key, []).append(s)
    return list(days.items())
