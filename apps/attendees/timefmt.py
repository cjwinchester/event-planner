"""AP-style times, as attendee emails and the old join-link doc use them."""

from __future__ import annotations

from datetime import datetime


def ap_time(dt: datetime, *, meridiem: bool = True) -> str:
    hour = dt.hour % 12 or 12
    text = f"{hour}:{dt.minute:02d}" if dt.minute else str(hour)
    return f"{text} {'a.m.' if dt.hour < 12 else 'p.m.'}" if meridiem else text


def ap_range(start: datetime, end: datetime) -> str:
    """'10 - 10:45 a.m.', '11 a.m. - 12 p.m.'"""
    same = (start.hour < 12) == (end.hour < 12)
    return f"{ap_time(start, meridiem=not same)} - {ap_time(end)}"
