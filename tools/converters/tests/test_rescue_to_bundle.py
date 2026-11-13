"""Synthetic fixture only — no real people."""

import jsonschema
import pytest

from apps.publishing.bundle import bundle_schema
from tools.converters.rescue_to_bundle import combined_parts, convert

SOURCE = {
    "conference": "Sample Conf 2026",
    "slug": "sample-conf-2026",
    "start_date": "2026-03-06",
    "end_date": "2026-03-08",
    "timezone": "America/New_York",
    "conference_series": "Sample Conf",
    "location": {"venue_name": "Example Hotel", "city": "Indianapolis", "state": "IN"},
    "sessions": [
        {
            "session_id": "0000000a",
            "start_time": "2026-03-07T09:00:00",
            "end_time": "2026-03-07T10:00:00",
            "room": "Hall E",
            "session_title": "Saturday panel",
            "session_type": "Panel",
            "speaker_ids": ["aaaa0001"],
            "speakers": "Ann Example, Other Paper",
            "description": "Line one.\n\nLine two.",
        },
        {
            "session_id": "0000000b",
            "start_time": "2026-03-08T09:00:00",
            "end_time": "2026-03-08T10:00:00",
            "room": "Hall E-F",
            "session_title": "Sunday keynote (after DST)",
            "session_type": "Special event",
            "speaker_ids": [],
            "speakers": "",
            "description": "",
        },
        {
            "session_id": "0000000c",
            "start_time": "2026-03-07T11:00:00",
            "end_time": "2026-03-07T12:00:00",
            "room": "Hall F",
            "session_title": "Lab",
            "session_type": "Hands-on training",
            "speaker_ids": [],
            "speakers": "",
            "description": "",
        },
    ],
    "speakers": [
        {
            "id": "aaaa0001",
            "first_name": "Ann",
            "last_name": "Example",
            "name": "Ann Example",
            "affiliation": "Daily Example",
            "bio": "Bio.",
        }
    ],
}


def test_convert_produces_valid_bundle_with_dst_and_combined_rooms():
    bundle, notes = convert(SOURCE, org_slug="sample", org_name="Sample Org")
    jsonschema.validate(bundle, bundle_schema())
    by_title = {s["title"]: s for s in bundle["sessions"]}
    assert by_title["Saturday panel"]["starts_at"] == "2026-03-07T14:00:00Z"  # EST, UTC-5
    assert by_title["Sunday keynote (after DST)"]["starts_at"] == "2026-03-08T13:00:00Z"  # EDT, UTC-4
    assert by_title["Saturday panel"]["id"] == 0xA
    assert by_title["Saturday panel"]["speakers"][0]["affiliation"] == "Other Paper"
    assert by_title["Saturday panel"]["description"] == "<p>Line one.</p><p>Line two.</p>"
    rooms = {r["name"]: r for r in bundle["rooms"]}
    assert rooms["Hall E-F"]["parts"] == ["r-hall-e", "r-hall-f"]
    assert any("Combined room" in n for n in notes)


def test_combined_parts_patterns():
    names = {"Salon 1", "Salon 2", "Salon 3", "Salon 1-3", "Ballroom A", "Ballroom C"}
    assert combined_parts("Salon 1-3", names) == ["Salon 1", "Salon 2", "Salon 3"]
    assert combined_parts("Ballroom A-C", names) == ["Ballroom A", "Ballroom C"]  # only parts in use
    assert combined_parts("Foyer", names) == []


@pytest.mark.django_db
def test_import_links_combined_rooms(world):
    from apps.publishing.importer import import_bundle

    bundle, _ = convert(SOURCE, org_slug="other", org_name="Other")
    event = import_bundle(bundle, world.other_org).event
    combo = event.event_rooms.get(room__name="Hall E-F").room
    assert {r.name for r in combo.parts.all()} == {"Hall E", "Hall F"}


def test_canceled_titles_and_room_order():
    from tools.converters.rescue_to_bundle import natural_key, split_canceled

    assert split_canceled("CANCELLED - Skills as tipsheets") == ("Skills as tipsheets", True)
    assert split_canceled("(Canceled) Lab") == ("Lab", True)
    assert split_canceled("Cancel culture and the press") == ("Cancel culture and the press", False)
    assert sorted(["White River E", "201", "104", "White River B", "1001"], key=natural_key) == [
        "104",
        "201",
        "1001",
        "White River B",
        "White River E",
    ]
