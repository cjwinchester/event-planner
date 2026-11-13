# REST API (v1)

Base path `/api/v1/`. Interactive OpenAPI docs: `/api/docs/` (schema: `/api/schema/`).
Auth: session (browser) or token (`Authorization: Token <key>`; create tokens in the
admin under *Auth Token*). Lists are paginated (`?limit=&offset=`) and support
`?search=`, `?ordering=` and the filters listed per endpoint.

Tenancy: every org-owned endpoint returns only rows from your organizations. Reads need
VIEWER, writes need STAFF (integrations: ADMIN). Related IDs in request bodies must belong to
your organizations, or the request fails with 400. Model validation (including scheduling
conflicts) runs on every write.

| Endpoint | What |
|---|---|
| `organizations/` | Your organizations |
| `calendar/` | Upcoming events across all orgs (NETWORK/PUBLIC visibility) |
| `public/events/` | Unauthenticated: PUBLIC upcoming events |
| `venues/`, `rooms/`, `floorplans/` | Shared venue directory (members read, staff write) |
| `series/`, `events/` | Org-scoped. Event actions below |
| `events/{id}/bundle/` | Event Bundle; `?audience=internal` includes unscheduled sessions and workflow fields |
| `events/{id}/grid/` | Everything the drag-and-drop planner needs: rooms, time slots, placed items, unscheduled pool, conflicts |
| `events/{id}/conflicts/` | All conflicts (errors + warnings) |
| `events/{id}/publish/` | POST `{formats?}` → 202, queues a publish run |
| `events/{id}/copy-sessions/` | POST `{source_event, session_ids?, include_speakers, keep_schedule}` |
| `event-rooms/`, `tracks/`, `time-slots/` | Per-event setup (`?event=`) |
| `sessions/` | CRUD (`?event=&status=&title_status=&is_stock=&ready=1`). Room/time are read-only here |
| `sessions/{id}/place/` | POST `{room, starts_at, ends_at, dry_run}` → 200 `{ok, saved, conflicts}`, or 409 if blocked |
| `session-speakers/` | Attach speakers to sessions (speaker conflicts are checked) |
| `logistics/`, `logistics/{id}/place/` | Same contract as sessions |
| `speakers/` | Shared directory; private fields only with a share grant |
| `speakers/me/` | Speaker self-service: GET (claims a profile matching your login email) / PATCH |
| `speakers/me/engagements/` | Your past and upcoming sessions |
| `speakers/me/grants/` | GET / POST `{organization}`; DELETE `speakers/me/grants/{org_id}/` |
| `my-pitches/` | Speaker: POST a pitch to one or more events `{title, description, events, co_speakers?, answers?}`; GET your pitches (decisions appear only once staff inform) |
| `pitch-reviews/` | Staff review (`?event=&status=`); actions: `decide/` `{ids, decision}`, `inform/` `{ids}`, `{id}/accept/`, `{id}/merge/` `{ids}`, `{id}/vote/` `{vote, comment}` |
| `companies/` | Shared company directory |
| `sponsorships/`, `exhibitors/` | Org-scoped commercial records |
| `publish-runs/` | Publish history with file URLs |
| `reports/`, `reports/{slug}/?series=` | Plug-in reports (pace, attendee dispersion, event locations) |
| `ask/` | POST `{organization, question}` → natural-language answer (501 until an LLM provider is configured) |

## Public feeds (no auth, `/feeds/`)

Only for events with `publish_schedule` on; they contain ready sessions only.

```
/feeds/<org>/<event>/bundle.json
/feeds/<org>/<event>/schedule.{ics,csv,txt,pdf,schedule_ui}
/feeds/<org>/<event>/tracks/<track-slug>.ics
/feeds/<org>/<event>/sessions/<public-id>.ics
```

Static copies are written by `publish` to the org's storage target (S3 or local) under
stable paths; see `PublishRun.files`.

## Planner contract (for the phase-2 grid UI)

1. `GET events/{id}/grid/` once.
2. While dragging: `POST sessions/{id}/place/` with `dry_run: true` to preview conflicts.
3. On drop: the same call without `dry_run`. A 409 means nothing was saved; show
   `conflicts[].message`. A 200 may still carry warnings (plenary overlap, capacity).
4. Unplace: `place` with `room`, `starts_at`, `ends_at` all null.
