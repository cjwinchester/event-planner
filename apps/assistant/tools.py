"""Read-only query tools the LLM may call — provider-neutral.

Each tool is a plain Python function plus a JSON Schema (strict: every
object sets additionalProperties=false and lists `required`), so it can be
exposed to any LLM provider and unit-tested without one.

Privacy rules (enforced here, not by the model):
  * The asking organization sees all of its own sessions.
  * Other organizations' sessions appear only if *published*: ready, on an
    event with publish_schedule and NETWORK/PUBLIC visibility.
  * Speakers are returned with public profile fields only — never email,
    phone, form answers, contracts or registrations.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db.models import Q

from apps.core.models import SHARED_VISIBILITIES
from apps.program.models import Session
from apps.speakers.models import SpeakerProfile

MAX_LIMIT = 50


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    func: Callable[..., Any]


def _visible_sessions(organization):
    published_elsewhere = Q(
        event__publish_schedule=True,
        event__visibility__in=SHARED_VISIBILITIES,
        title_status=Session.EditStatus.EDITED,
        room__isnull=False,
        starts_at__isnull=False,
    )
    return Session.objects.active().filter(Q(event__organization=organization) | published_elsewhere)


def search_sessions(
    organization,
    *,
    text: str = "",
    series: str = "",
    event: str = "",
    year: int | None = None,
    session_type: str = "",
    track: str = "",
    speaker_affiliation: str = "",
    limit: int = 20,
) -> list[dict]:
    qs = _visible_sessions(organization).select_related("event__series", "session_type")
    if text:
        qs = qs.filter(Q(title__icontains=text) | Q(description__icontains=text))
    if series:
        qs = qs.filter(Q(event__series__slug__iexact=series) | Q(event__series__name__icontains=series))
    if event:
        qs = qs.filter(Q(event__slug__iexact=event) | Q(event__name__icontains=event))
    if year:
        qs = qs.filter(event__start_date__year=year)
    if session_type:
        qs = qs.filter(session_type__name__icontains=session_type)
    if track:
        qs = qs.filter(Q(tracks__slug__iexact=track) | Q(tracks__name__icontains=track))
    if speaker_affiliation:
        qs = qs.filter(session_speakers__speaker__affiliation__icontains=speaker_affiliation)
    qs = qs.distinct().prefetch_related("session_speakers__speaker", "tracks").order_by("-event__start_date", "title")
    return [
        {
            "event": s.event.name,
            "year": s.event.start_date.year,
            "session_id": s.public_id,
            "title": s.title,
            "type": s.session_type.name if s.session_type else "",
            "tracks": [t.name for t in s.tracks.all()],
            "speakers": [
                {
                    "name": ss.speaker.full_name,
                    "affiliation": ss.affiliation_override or ss.speaker.affiliation,
                    "role": ss.role,
                }
                for ss in s.session_speakers.all()
            ],
        }
        for s in qs[: min(limit, MAX_LIMIT)]
    ]


def search_speakers(
    organization, *, name: str = "", affiliation: str = "", text: str = "", limit: int = 20
) -> list[dict]:
    qs = SpeakerProfile.objects.filter(is_active=True, merged_into__isnull=True)
    if name:
        for part in name.split():
            qs = qs.filter(Q(first_name__icontains=part) | Q(last_name__icontains=part))
    if affiliation:
        qs = qs.filter(affiliation__icontains=affiliation)
    if text:
        qs = qs.filter(Q(bio__icontains=text) | Q(job_title__icontains=text))
    visible = _visible_sessions(organization)
    out = []
    for sp in qs.order_by("last_name", "first_name")[: min(limit, MAX_LIMIT)]:
        sessions = visible.filter(session_speakers__speaker=sp).select_related("event").distinct()[:10]
        out.append(
            {
                "speaker_id": sp.public_id,
                "name": sp.full_name,
                "affiliation": sp.affiliation,
                "job_title": sp.job_title,
                "bio": sp.bio[:600],
                "sessions": [{"event": s.event.name, "title": s.title} for s in sessions],
            }
        )
    return out


def _schema(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


_str = {"type": "string"}
_limit = {"type": "integer", "description": f"Max results (1-{MAX_LIMIT})."}

TOOLS = [
    Tool(
        name="search_sessions",
        description=(
            "Search conference sessions visible to the user's organization (its own plus other organizations' "
            "published sessions). Every filter is optional: pass an empty string (or 0 for year) to skip it. "
            "Text matches title and description. Returns event, year, title, type, tracks and speakers with "
            "affiliations."
        ),
        input_schema=_schema(
            {
                "text": {**_str, "description": "Words to find in title/description, e.g. 'python'."},
                "series": {**_str, "description": "Event series slug or name, e.g. 'nicar'."},
                "event": {**_str, "description": "Event slug or name, e.g. 'nicar-2026'."},
                "year": {"type": "integer", "description": "Event year, or 0 for any year."},
                "session_type": {**_str, "description": "e.g. 'Hands-on', 'Panel'."},
                "track": {**_str, "description": "Track slug or name."},
                "speaker_affiliation": {**_str, "description": "Substring of a speaker's affiliation."},
                "limit": _limit,
            }
        ),
        func=search_sessions,
    ),
    Tool(
        name="search_speakers",
        description=(
            "Search the shared speaker directory (public profile fields only). Every filter is optional: pass an "
            "empty string to skip it. Returns name, affiliation, title, a short bio and visible sessions."
        ),
        input_schema=_schema(
            {
                "name": {**_str, "description": "Full or partial name."},
                "affiliation": {**_str, "description": "Substring of the affiliation, e.g. 'University'."},
                "text": {**_str, "description": "Words to find in bio or job title."},
                "limit": _limit,
            }
        ),
        func=search_speakers,
    ),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def run_tool(organization, name: str, arguments: dict) -> Any:
    """Execute a tool call for `organization`. Unknown names raise KeyError."""
    tool = TOOLS_BY_NAME[name]
    kwargs = {k: v for k, v in (arguments or {}).items() if k in tool.input_schema["properties"]}
    if kwargs.get("year") == 0:
        kwargs["year"] = None
    if "limit" in kwargs:
        kwargs["limit"] = max(1, min(int(kwargs["limit"] or 20), MAX_LIMIT))
    return tool.func(organization, **kwargs)
