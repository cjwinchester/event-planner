# Working in this repo (for AI agents and humans)

Read README.md, then docs/architecture.md. The roadmap and known gaps are in docs/continuing.md.

## Commands
- `uv run pytest` (needs PostGIS; `DATABASE_URL` in `.env`). On macOS, PDF tests need
  `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`; otherwise they skip.
- `uv run ruff check . && uv run ruff format --check .`
- `uv run python manage.py makemigrations --check --dry-run` must report no changes.

## Invariants (don't break these)
- **Tenancy**: every org-owned model has `ORG_PATH` and an `OrgScopedQuerySet` manager.
  Admin classes use `core.admin.OrgScopedAdmin`/`OrgScopedInline`. API viewsets use
  `api.base.OrgScopedViewSet` with `OrgModelSerializer`. Role checks go through
  `organizations.permissions`. Add a tenancy test for any new org-owned endpoint.
- **Privacy**: contracts/costs (`EventContract`), registrations, `EventSpeaker`, form answers
  and internal notes never reach a public bundle, feed or another org. Speaker private
  fields require a `SpeakerShareGrant` (`speakers.sharing`).
- **One schema**: exporters render only from the Event Bundle dict, and the importer
  accepts only bundles. Change `schemas/event-bundle/v1.json` additively.
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
