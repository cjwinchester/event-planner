"""Stable, unauthenticated public feeds rendered on request.

    /feeds/<org>/<event>/bundle.json        Event Bundle (canonical)
    /feeds/<org>/<event>/schedule.ics       all published sessions
    /feeds/<org>/<event>/tracks/<slug>.ics  one track
    /feeds/<org>/<event>/sessions/<id>.ics  one session ("add to calendar")
    /feeds/<org>/<event>/schedule.<fmt>     csv | txt | pdf | schedule_ui

These mirror the static files the publisher writes, for clients that want
a live URL. Only events with publish_schedule=True are served; content is
the PUBLIC bundle (ready sessions only). Short cache headers.
"""

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.cache import patch_cache_control

from apps.events.models import Event

from .cache import cached_render, public_bundle
from .exporters import ExportContext, get_exporter
from .exporters.ics import build_calendar

CACHE_SECONDS = 300


def _event(org_slug, event_slug) -> Event:
    event = get_object_or_404(
        Event.objects.select_related("organization", "series"), organization__slug=org_slug, slug=event_slug
    )
    if not event.publish_schedule:
        raise Http404("Not published")
    return event


def _cached(resp):
    patch_cache_control(resp, public=True, max_age=CACHE_SECONDS)
    resp["Access-Control-Allow-Origin"] = "*"
    return resp


def _host():
    return settings.EVENT_PLANNER.get("CALENDAR_HOST", "event-planner")


def bundle_json(request, org_slug, event_slug):
    bundle = public_bundle(_event(org_slug, event_slug))
    return _cached(JsonResponse(bundle, json_dumps_params={"ensure_ascii": False}))


def schedule_format(request, org_slug, event_slug, fmt):
    if fmt not in {"csv", "txt", "pdf", "schedule_ui", "ics"}:
        raise Http404
    event = _event(org_slug, event_slug)
    exporter = get_exporter(fmt)

    def render(bundle):
        main = exporter.render(bundle, ExportContext(calendar_host=_host()))[0]
        return main.content, main.content_type

    # Rendered once per schedule version (the PDF in particular is expensive).
    content, content_type = cached_render(event, fmt, render)
    return _cached(HttpResponse(content, content_type=content_type))


def track_ics(request, org_slug, event_slug, track_slug):
    bundle = public_bundle(_event(org_slug, event_slug))
    track = next((t for t in bundle["tracks"] if t["id"] == track_slug), None)
    if track is None:
        raise Http404
    sessions = [s for s in bundle["sessions"] if track_slug in s.get("tracks", [])]
    body = build_calendar(bundle, sessions, f"{bundle['event']['name']}: {track['name']}", _host())
    return _cached(HttpResponse(body, content_type="text/calendar; charset=utf-8"))


def session_ics(request, org_slug, event_slug, session_id: int):
    bundle = public_bundle(_event(org_slug, event_slug))
    sessions = [s for s in bundle["sessions"] if s["id"] == session_id]
    if not sessions:
        raise Http404
    resp = HttpResponse(
        build_calendar(bundle, sessions, sessions[0]["title"], _host()), content_type="text/calendar; charset=utf-8"
    )
    resp["Content-Disposition"] = f'attachment; filename="{event_slug}-{session_id}.ics"'
    return _cached(resp)
