"""Venue geocoding — pluggable, free providers first.

`geocode(venue)` tries providers in order and returns a GeocodeResult or
None. US addresses go to the Census Bureau geocoder (free, no key — ported
from ire-data-dashboard's planning app); everything else to OpenStreetMap
Nominatim (free; requires an identifying User-Agent and ≤1 request/sec).
Add providers by appending to PROVIDERS.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from django.conf import settings
from django.contrib.gis.geos import Point

logger = logging.getLogger(__name__)


@dataclass
class GeocodeResult:
    point: Point
    matched_address: str
    source: str


def _census(venue) -> GeocodeResult | None:
    if venue.country.upper() != "US" or not venue.address1:
        return None
    resp = requests.get(
        "https://geocoding.geo.census.gov/geocoder/locations/address",
        params={
            "street": venue.address1,
            "city": venue.city,
            "state": venue.region,
            "zip": venue.postal_code,
            "benchmark": "Public_AR_Current",
            "format": "json",
        },
        timeout=10,
    )
    resp.raise_for_status()
    matches = resp.json()["result"]["addressMatches"]
    if not matches:
        return None
    m = matches[0]
    return GeocodeResult(Point(m["coordinates"]["x"], m["coordinates"]["y"], srid=4326), m["matchedAddress"], "census")


def _nominatim(venue) -> GeocodeResult | None:
    query = ", ".join(p for p in (venue.address1, venue.city, venue.region, venue.postal_code, venue.country) if p)
    if not query:
        return None
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "jsonv2", "limit": 1},
        headers={"User-Agent": settings.EVENT_PLANNER["HTTP_USER_AGENT"]},
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    r = rows[0]
    return GeocodeResult(Point(float(r["lon"]), float(r["lat"]), srid=4326), r.get("display_name", ""), "nominatim")


PROVIDERS = [_census, _nominatim]


def geocode(venue) -> GeocodeResult | None:
    for provider in PROVIDERS:
        try:
            result = provider(venue)
        except requests.RequestException as exc:
            logger.warning("Geocoder %s failed for %s: %s", provider.__name__, venue, exc)
            continue
        if result:
            return result
    return None


def geocode_and_save(venue) -> bool:
    result = geocode(venue)
    if not result:
        return False
    venue.location = result.point
    venue.geocode_source = result.source
    venue.save(update_fields=["location", "geocode_source", "updated_at"])
    return True
