# event-planner

Open-source, multi-tenant event planning for nonprofits with small staffs —
built first for journalism support organizations (JSOs) running conferences.
It puts content planning (pitches, sessions, speakers) and logistics planning
(venues, rooms, the schedule grid, contracts, sponsors) in one place, shares
what should be shared across participating organizations, and publishes
schedules in every format attendees and apps need.

The goal is to replace spreadsheet + Apps Script workflows (the "SuperGrid")
and the paid vendors nonprofits rent every year (Sessionize, Sched,
Guidebook...).

> **Status: phase 2 in progress.** Phase 1 delivered the data model, org-scoped
> Django admin, REST API, publishing/import pipeline, connector framework and
> reports. Phase 2 has started with the staff planner at `/plan/`: a
> drag-and-drop schedule grid with an unscheduled pool, conflict previews,
> undo, quick-edit and time-slot panels. Pitch dashboard, portals and maps are
> next. See [docs/continuing.md](docs/continuing.md).

## Quick start

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), PostgreSQL with
PostGIS, GDAL/GEOS (GeoDjango), and Pango (only for PDF export).

```bash
uv sync --dev
cp .env.example .env            # set DATABASE_URL, FIELD_ENCRYPTION_KEYS
docker compose up -d db         # or use your own PostGIS
uv run python manage.py migrate
uv run python manage.py createsuperuser
bash bin/tailwindcss-build.sh   # builds static/css/app.css (standalone CLI, no Node)
uv run python manage.py runserver
```

- Planner: <http://localhost:8000/plan/>, Admin: <http://localhost:8000/admin/>, API docs: <http://localhost:8000/api/docs/>
- Background worker (publishing, syncs): `uv run python manage.py db_worker`
- Tests: `uv run pytest` (browser tests need `uv run playwright install chromium` once;
  skip them with `-m "not browser"`) · Lint: `uv run ruff check . && uv run ruff format --check .`

Try it with the sample data:

```bash
uv run python manage.py shell -c "from apps.organizations.models import Organization as O; O.objects.get_or_create(slug='sample-org', defaults={'name': 'Sample Org'})"
uv run python manage.py import_event apps/publishing/tests/fixtures/sample-bundle.json --org sample-org
# turn on "publish schedule" for the event in the admin, then:
uv run python manage.py publish_event sample-org/sample-conf-2026
```

**macOS notes**
- PDF export needs Homebrew's Pango on the loader path:
  `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` (otherwise the PDF test
  is skipped and publishing records PDF as a failed format).
- If GeoDjango can't find GDAL/GEOS, set `GDAL_LIBRARY_PATH` / `GEOS_LIBRARY_PATH` in `.env`.
- Postgres.app may refuse connections from tools it doesn't recognize; use a
  password-protected role in `DATABASE_URL`, or the docker-compose database.

## Architecture at a glance

```
config/            settings (base/dev/test/prod), urls
apps/
  core/            tenancy (ORG_PATH, OrgScopedQuerySet), Visibility, admin mixins, encrypted field
  accounts/        email-login User; Google Workspace SSO adapter
  organizations/   Organization (tenant) + Membership roles (admin/staff/viewer)
  venues/          shared venue directory: Venue (PostGIS), Floorplan, Room (combinable), capacities
  events/          EventSeries, Event, EventVenue, EventRoom; private EventContract + pickup, VenueRFP + Concessions; Registrations
  program/         Track, SessionType, SkillLevel, TimeSlot, Session, LogisticsItem; conflicts, scheduling, copying
  speakers/        global SpeakerProfile + links, speaker-controlled share grants, per-event EventSpeaker
  pitches/         Pitch → PitchTarget (per event), votes, custom questions; review workflow services
  commerce/        Company, sponsorships (event/track/session/special), exhibitors, comp tickets, invoices
  integrations/    connector framework + MemberSuite, MemberClicks, Zoom, Vimeo, S3, local storage
  publishing/      Event Bundle builder, exporters (JSON/CSV/TXT/ICS/PDF/UI), publish service, importer, feeds
  reporting/       plug-in reports: registration pace, attendee dispersion, event locations
  assistant/       LLM query service (provider-agnostic) + read-only, privacy-scoped tools
  api/             DRF v1: viewsets, serializers, tenancy-safe base classes
  planner/         staff UI: schedule grid page (static/js/grid.js) + HTMX side panels
schemas/event-bundle/v1.json   the one interchange format
```

Full model map, tenancy and data-flow diagrams: [docs/architecture.md](docs/architecture.md).

## Key decisions (and why)

| Decision | Why |
|---|---|
| **One shared database, row-level tenancy** (`ORG_PATH` on every org-owned model) | Cross-org sharing (speakers, venues, calendar) is a core feature; schema-per-tenant would fight it. Admin, API and services all scope through the same `OrgScopedQuerySet`. |
| **Shared directories vs. org-private data** | Venues, rooms, speaker profiles and companies are global (one org's work helps all). Contracts, costs, notes, registrations, engagement details are org-private. **Cost data is never shared.** |
| **Speakers control sharing** | Contact details (email/phone/location) are visible only to orgs with a `SpeakerShareGrant`. Pitching grants by default; speakers can revoke; automatic grants never override a revocation. |
| **One schema for everything** | The Event Bundle (`schemas/event-bundle/v1.json`) is what the API serves, what every published file is rendered from, and the *only* import format. Legacy data is transformed upstream. |
| **Times are UTC; events carry an IANA timezone** | DST-correct ICS/feeds; the admin shows/accepts event-local times. |
| **Overlap-based conflict checks, combined-room aware** | Sessions rarely align to identical start times; airwall rooms ("E-F") occupy their parts. One locked write path (`program.scheduling`) prevents races. |
| **"Ready" is computed** | Placed + timed + edited title + not canceled. Only ready sessions are ever published. |
| **Decide, then inform** (pitches) | Staff queue accept/decline decisions and notify in one batch; speakers see "submitted" until informed. |
| **Django built-in Tasks + `django-tasks-db`** | No Redis to run; swap backends by settings. |
| **Connectors declare capabilities and credential scopes** | Integrations toggle per org; an AMS is swappable (MemberSuite ↔ MemberClicks) behind the same service code. |
| **Publishing keeps today's schedule UI working** | The `schedule_ui` exporter emits the existing conference-schedule-ui JSON (validated against its schema) until the UI reads the bundle directly. |

## Documentation

- [docs/architecture.md](docs/architecture.md) — models, relationships, tenancy, data flows
- [docs/api.md](docs/api.md) — REST endpoint map, auth, planning/placement contract
- [docs/event-bundle.md](docs/event-bundle.md) — the interchange schema, for anyone writing an upstream converter
- [docs/continuing.md](docs/continuing.md) — roadmap, known gaps, vendor-parity checklist, how to extend
- [docs/spec/initial-prompt.txt](docs/spec/initial-prompt.txt) — the original specification
- [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md) — conventions for AI agents working in this repo

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
