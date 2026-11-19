# Working in this repo (for AI agents and humans)

Read README.md, then docs/architecture.md. The roadmap and known gaps are in docs/continuing.md.

## Terms (use them precisely: they decide permissions)
- **Organization**: an event-planning organization using this app (the tenant: IRE, sample-org). Its people
  are **staff** with `Membership` roles (admin/staff/viewer); it owns events and its side of every deal.
  In speaker/company-facing text, "event organizer" is fine; never use "organization" for a company or an employer.
- **Company**: a sponsor, exhibitor or vendor (`commerce.Company`). Its people are `CompanyContact`s:
  **managers** (company-wide or scoped to specific Organizations) and **members**.
- **Affiliation**: a speaker's employer/newsroom (a text field), not an Organization or a Company.
- **Deal**: a `Sponsorship` or `ExhibitorBooking`: private between one Organization and one Company.

## Commands
- Local PostGIS: `bash bin/devdb.sh` (cluster on 127.0.0.1:5433, data in gitignored `.pgdata/`;
  run it again after a reboot).
- `uv run pytest` (needs PostGIS; `DATABASE_URL` in `.env`). On macOS, PDF tests need
  `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`; otherwise they skip.
- `uv run ruff check . && uv run ruff format --check .`
- Browser tests (Playwright, marker `browser`): `uv run playwright install chromium` once.
  Set `GRID_SCREENSHOT=/path.png` to save a screenshot of the grid test.
- CSS: `bash bin/tailwindcss-build.sh` → `static/css/app.css` (gitignored). Grid geometry
  is plain CSS in `static/css/grid.css` so drag math never depends on the build.
- `uv run python manage.py makemigrations --check --dry-run` must report no changes.

## Invariants (don't break these)
- **Tenancy**: every org-owned model has `ORG_PATH` and an `OrgScopedQuerySet` manager.
  Admin classes use `core.admin.OrgScopedAdmin`/`OrgScopedInline`. API viewsets use
  `api.base.OrgScopedViewSet` with `OrgModelSerializer`. Role checks go through
  `organizations.permissions`. Add a tenancy test for any new org-owned endpoint.
- **Privacy**: contracts/costs (`EventContract`), RFPs and concessions, registrations, `EventSpeaker`, form answers,
  internal notes, attendee passes and **join links** (`Session.live_url`, `VirtualMeeting`, `Room.join_url`) never
  reach a public bundle, feed or another org. Virtual venues are owned (`Venue.owner`) and hidden from other orgs. Speaker private
  fields require a `SpeakerShareGrant` (`speakers.sharing`).
- **One schema**: exporters render only from the Event Bundle dict, and the importer
  accepts only bundles. Change `schemas/event-bundle/v1.json` additively.
- **Pitch review is blind**: never expose other staff's votes for a pitch unless
  `pitches.services.votes_visible()` says so. Only org admins finalize. Call merging pitches
  "combo" in anything user-facing.
- **Published speakers are confirmed speakers**: public bundles include only `SessionSpeaker.is_confirmed` rows.
  Speakers confirm in the portal (or staff mark it); speaker portal edits/confirmations/withdrawals go through
  `program.speaker_actions` (deadline check + `SpeakerUpdate` log).
- **Schedules show per-event copies**: speaker affiliation/title/bio/headshot come from `EventSpeaker`, company
  name/description/website/logo from the deal (`CompanyListing` on `Sponsorship`/`ExhibitorBooking`), copied from
  the shared record when created. Never read those from the shared profile in exports. Names and pronouns stay live.
- **Company access** goes through `commerce.access`: members see no deals; managers see deals for the orgs in
  their scope (all if `company_wide`); an org only ever adds itself to someone's scope
  (`add_contact_for_org`). Only company-wide managers (or orgs, while unclaimed) edit the shared company profile.
- **Shared speaker profiles**: edits go through `speakers.permissions` (a claimed profile is the speaker's;
  an unclaimed one's email only by the creating org, since the email decides who can claim it).
- **Public reads use the cache**: feeds and pass pages call `publishing.cache.public_bundle` (deep copy; never
  mutate the cached dict). If the bundle gains data from a new model, add it to `content_version`.
- **Scheduling writes** go through `program.scheduling.place_*` (it locks the event and
  checks conflicts). Don't save room/time changes directly in new code paths.
- **Times** are stored in UTC; display and accept them in `event.timezone`.
- **Stable IDs**: never change `Session.public_id`, `SpeakerProfile.public_id` or
  `Event.slug` after publishing.
- **Connectors** stay free of ORM writes; services upsert. Credentials live only in
  `IntegrationConfig.credentials` (encrypted).

## Conventions
- Django 6.1 / Python 3.12, ruff (line length 120). Docstrings explain *why*.
- Tests live in `apps/<app>/tests/`, and shared fixtures are in the root `conftest.py`
  (`world`, `make_session`, `at()`). Fixtures must not contain real personal data.
- `data-scripts-old/` is gitignored and contains real PII. Never commit it or copy from it into fixtures.
