# Continuing development

## What exists

**Phase 2 (in progress):** staff planner at `/plan/`. It has an event list and the schedule
grid. In the grid you can:
- switch days with tabs;
- drag sessions in from the unscheduled pool and back out;
- move and resize, with snapping to time slots;
- see local conflict previews and server-confirmed placement;
- undo the last move;
- get a stale-data banner when someone else edits;
- see combined rooms blocked in their part columns.

The side panels cover session quick edit, keyboard placement, speakers, a new-session
form, and time slots (add/edit/delete/copy a day). There are also venue RFPs and
concession tracking (admin + API). Browser tests use Playwright.

**Phase 1:**

Steps 1–5 of [the spec](spec/initial-prompt.txt): project setup, full models and
migrations, org-scoped admin with validation, REST API plus export and publishing services,
and these docs. The suite has 75 tests; `uv run pytest` must stay green, and ruff must stay
clean.

## Phase 2 roadmap (suggested order)

1. ~~Staff UI shell~~ and ~~drag-and-drop schedule grid~~ (done). Follow-ups: event hub
   page, logistics quick-edit panel (logistics cards currently open the admin), an
   all-days overview, printing the grid, and live push instead of polling.
2. **Venue negotiation view**: RFP/concession comparison across hotels in the planner.
3. **Pitch review dashboard**: filters, per-staffer votes, queue/inform buttons (the admin
   actions already implement the logic).
4. **Notifications**: implement the `notifier` hook for `pitches.services.inform`
   (templated email to pitchers). Add speaker-info form invites (`FormQuestion.Form.SPEAKER_INFO`).
5. **Speaker portal**: profile, share grants, engagements, pitch form (API exists:
   `speakers/me/*`, `my-pitches/`).
6. **Sponsor/exhibitor portal**: `CompanyContact.user` exists, but no endpoints or views
   yet. Needs a company profile, booth-staff tickets within entitlement, and deliverables uploads.
7. **Maps**: Leaflet with `L.CRS.Simple` floorplans (`Floorplan.leaflet_bounds`, room
   `map_x/map_y`), venue maps, and attendee heat maps from the `attendee-dispersion` report.
8. **Pace dashboard**: chart `reports/series-pace/`.
9. **Schedule UI**: either keep publishing the `schedule_ui` adapter with the existing
   conference-schedule-ui build (`SCHEDULE_UI_DIST_DIR`), or port that UI to read the
   Event Bundle directly and retire the adapter.
10. **GraphQL** (strawberry-django), read-only first, if mobile clients need it.

## Known gaps and TODOs

| Area | Gap |
|---|---|
| MemberSuite | Registration address columns are unverified against a live tenant (see connector docstring). No write-back. |
| MemberClicks, Zoom | Stubs with documented plans. Vimeo can list videos but not yet attach recordings to sessions. |
| Room pickup | Manual `RoomPickupSnapshot` entry only. Hotel portals usually need a login, so automate later if at all. |
| Hotel spec sheet | Fields exist on sessions and logistics (setup, headcount, A/V, F&B, DNP via `is_public`). The "For Hotel" exporter is not written yet. |
| Speaker dedupe | `SpeakerProfile.merged_into` exists; there's no merge service or UI yet. |
| Payments | `commerce/payments.py` has the interface and a manual provider only. No webhook endpoint yet. |
| Venue directory | Any staff user can edit shared venues and rooms; consider moderation or edit history. |
| Cross-org room clashes | Two orgs' events in the same hotel room at the same time are not detected. |
| Import | Floorplan images aren't downloaded. Historical logistics are imported as non-blocking. |
| UI bundle exporter | Rewrites `DATA_URL` only; page metadata (title/OG tags) still comes from the UI build. |
| Scheduling jobs | No cron yet: nightly registration syncs, `prune_db_task_results`. Use the host's scheduler to run management commands. |
| Media kit / social cards, Canva | Not started (lower priority in the spec). |
| LLM assistant | Interface, tools and a Claude provider skeleton exist (`uv sync --extra ai`, `LLM_PROVIDER=anthropic`). Not yet run against the live API; add query logging and evals before relying on it. |

## Vendor-parity checklist (replace Sessionize / Sched / Guidebook)

- [x] Call for proposals with custom questions, multi-event submission
- [x] Review: votes, queue decisions, bulk inform, accept → session, merge
- [x] Speaker profiles across events; speaker-controlled data sharing
- [x] Schedule grid data + conflict checking (UI pending)
- [x] Public feeds: JSON, ICS (event/track/session), CSV, TXT, PDF
- [x] Stable IDs for favorites / calendar updates
- [ ] Speaker and attendee notifications (email)
- [ ] Embeddable schedule widget / attendee web app reading the bundle
- [ ] Mobile app API polish (auth flows, push), offline bundle
- [ ] Session feedback / ratings, attendance counts (`SessionRegistration` / Zoom attendance)
- [ ] Sponsor lead retrieval, exhibitor portal

## How to extend

- **New connector**: subclass `integrations.base.BaseConnector`, set `slug`, `capabilities`
  and `credential_fields` (each with `scope=ORG|EVENT`), implement the capability protocol
  methods, decorate with `@register`, and import it in `integrations/connectors/__init__.py`.
  Return normalized dataclasses and keep ORM writes in `integrations/services.py`.
- **New export format**: subclass `publishing.exporters.base.Exporter`. Render from the bundle
  dict only, never the ORM. Implement `filenames()` and `render()`, decorate with `@register`, and
  import it in `exporters/__init__.py`.
- **New report**: subclass `reporting.registry.Report` with `slug`, `scope` and `compute(obj)`,
  then `@register`. It appears at `/api/v1/reports/<slug>/`.
- **New org-owned model**: give it `ORG_PATH` and `objects = OrgScopedManager()` (or a
  queryset subclassing `OrgScopedQuerySet`). Register admin with `OrgScopedAdmin` and API with
  `OrgScopedViewSet` + `OrgModelSerializer`. Add a tenancy test.
- **Bundle schema change**: edit `schemas/event-bundle/v1.json` (additive only), `bundle.py`,
  `importer.py`, and the round-trip test.
