# Event Bundle v1

[`schemas/event-bundle/v1.json`](../schemas/event-bundle/v1.json) is the single
interchange format:

- the API serves it (`/api/v1/events/{id}/bundle/`, `/feeds/<org>/<event>/bundle.json`);
- every published file (CSV, TXT, ICS, PDF, schedule-ui JSON) is rendered **from** it;
- it is the **only** format `import_event` accepts.

Converting legacy data (SuperGrid sheets, old schedule-ui feeds, other vendors' exports)
happens upstream: write a script that outputs a bundle, then
`manage.py import_event bundle.json --org <slug> --dry-run`.

## Shape

```json
{
  "schema_version": "1.0",
  "generated_at": "2027-01-10T18:00:00Z",
  "audience": "public",
  "organization": {"slug": "ire", "name": "Investigative Reporters & Editors"},
  "event": {
    "slug": "nicar-2027", "name": "NICAR 2027", "timezone": "America/Denver",
    "start_date": "2027-03-04", "end_date": "2027-03-07",
    "series": {"slug": "nicar", "name": "NICAR"},
    "venues": [{"id": "v1", "role": "primary", "name": "...", "address": {...}, "location": {"lat": 39.7, "lon": -104.9}}]
  },
  "rooms":    [{"id": "r12", "name": "Plaza B", "venue_id": "v1", "capacity": 250, "computer_os": ""}],
  "tracks":   [{"id": "public-records", "name": "Public records"}],
  "sessions": [{"id": 2001, "title": "...", "room_id": "r12", "starts_at": "2027-03-05T16:00:00Z",
                "ends_at": "2027-03-05T17:00:00Z", "tracks": ["public-records"],
                "speakers": [{"speaker_id": "4d273630", "role": "speaker", "affiliation": "BBC News"}]}],
  "speakers": [{"id": "4d273630", "first_name": "...", "last_name": "...", "session_ids": [2001]}],
  "logistics": [], "sponsors": [], "exhibitors": [], "feeds": {}
}
```

## Rules converters must follow

- **Times** are UTC with a trailing `Z`. Convert from the event's local time using
  `event.timezone`; don't just append `Z` to local times.
- **IDs are the upsert keys.** Session `id` is the stable public number (keep historical
  numbers). Speaker `id` is a stable string; reuse it across events for the same person, or
  include `email` (internal bundles only) so the importer can match existing profiles.
  Room and venue `id`s only need to be consistent within one bundle; rooms are matched by
  name within the venue.
- **Track ids** are slugs (`^[a-z0-9]+(?:[-_][a-z0-9]+)*$`).
- **HTML** in `description` / `bio` is sanitized on export; send simple HTML or plain text.
- **Speakers' social links**: `{"kind": "linkedin", "label": "LinkedIn", "url": "..."}`.
  The SuperGrid `Label=url|Label=url` format needs splitting upstream.
- `additionalProperties: false` everywhere: unknown keys fail validation, so typos surface.

## Versioning

Additive, optional fields can be added to v1. Renames, removals or meaning changes need
`v2.json` and a new exporter. Keep v1 output available while consumers migrate.
