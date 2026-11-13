"""Reusable validators."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from django.core.exceptions import ValidationError


def validate_timezone(value: str) -> None:
    """Accept only real IANA zone names ("America/Chicago"), never offsets or
    abbreviations — DST-correct ICS/feeds depend on it."""
    if not value:
        return
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(f"{value!r} is not an IANA timezone name (e.g. America/Chicago).") from exc
    if value not in available_timezones() and value != "UTC":
        raise ValidationError(f"{value!r} is not an IANA timezone name.")
