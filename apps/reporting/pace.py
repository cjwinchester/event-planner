"""Registration pace: compare cumulative registrations by days-before-event
across every event in a series (ported from ire-data-dashboard's
core/conference_series.py, simplified to this app's data model).

Source per event, in order of preference:
  1. events.Registration rows (registered_at; canceled excluded)
  2. events.RegistrationSnapshot (cumulative totals entered or imported)

Output (Chart.js-ready):
  {"series": [{"event", "label", "days_out": [...], "cumulative": [...], "final"}],
   "today": {"days_out", "rows": [{"event", "label", "count", "final"}]}}
"""

from __future__ import annotations

from collections import Counter

from django.utils import timezone

from apps.events.models import Event

from .registry import Report, ReportScope, register


def registration_curve(event: Event) -> list[tuple[int, int]]:
    """Sorted (days_out descending → 0) changepoints of the cumulative count."""
    regs = event.registrations.filter(canceled_at__isnull=True).values_list("registered_at", flat=True)
    if regs:
        tz = event.tz
        per_day = Counter((event.start_date - r.astimezone(tz).date()).days for r in regs)
        running, curve = 0, []
        for days_out in sorted(per_day, reverse=True):
            running += per_day[days_out]
            curve.append((days_out, running))
        return curve
    snaps = event.registration_snapshots.order_by("recorded_on")
    return [((event.start_date - s.recorded_on).days, s.total_registrations) for s in snaps]


def cumulative_at(curve: list[tuple[int, int]], days_out: int) -> int:
    """Cumulative count as of `days_out` days before the event."""
    count = 0
    for day, cumulative in curve:  # descending days_out
        if day < days_out:
            break
        count = cumulative
    return count


def series_pace(series) -> dict:
    events = list(series.events.exclude(status=Event.Status.CANCELED).order_by("start_date"))
    today = timezone.localdate()
    curves = {e.pk: registration_curve(e) for e in events}
    out_series = [
        {
            "event": e.slug,
            "label": str(e.start_date.year),
            "days_out": [d for d, _ in curves[e.pk]],
            "cumulative": [c for _, c in curves[e.pk]],
            "final": e.end_date < today,
        }
        for e in events
        if curves[e.pk]
    ]
    today_block = None
    if events:
        latest = events[-1]
        days_out = max((latest.start_date - today).days, 0)
        today_block = {
            "days_out": days_out,
            "rows": [
                {
                    "event": e.slug,
                    "label": e.name,
                    "count": cumulative_at(curves[e.pk], days_out),
                    "final": curves[e.pk][-1][1] if curves[e.pk] and e.end_date < today else None,
                }
                for e in reversed(events)
            ],
        }
    return {"series": out_series, "today": today_block}


@register
class SeriesPaceReport(Report):
    slug = "series-pace"
    title = "Registration pace"
    description = "Cumulative registrations by days before the event, for every event in a series."
    scope = ReportScope.SERIES

    def compute(self, obj, **params):
        return series_pace(obj)
