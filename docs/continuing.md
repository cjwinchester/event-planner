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
concession tracking (admin + API).

**Pitch review dashboard** (`/plan/<org>/<event>/pitches/`):
- **Table:** one row per pitch, filters, sorting, and keyboard review (`j`/`k`, `y`/`m`/`n`,
  `x`, Enter).
- **Blind voting:** other people's votes appear only after you vote, or once a decision
  exists.
- **Side panel:** description, custom answers, the pitcher's history with your
  organization, votes and comments, notes, tracks and assignee.
- **Bulk actions:** queue accept/decline, waitlist, assign, combo.
- **Finalize:** a review page; only org admins can finalize, which creates sessions.
- **Staff-entered pitches:** staff can add a pitch on someone's behalf. That grants only
  the weaker ENGAGEMENT share, so a speaker's revocation is never overridden.
- **"Combo"** is the house term for merging pitches (stored as status `merged`).
- Pitchers aren't notified yet (deferred).

Browser tests use Playwright.

**New event** (`/plan/new/`, "+ New event" on the dashboard; staff): optionally copy from an
existing event, ticking parts (`events.copying.PARTS`): details & settings, venues & rooms,
time slots, tracks, recurring (stock) sessions, logistics, pitch/speaker form questions,
sponsorship levels & exhibitor packages. Timed items move by event day (day 1 → day 1, same
local time); logistics are re-placed via `place_logistics_item`. Registrations, contracts,
RFPs, sponsorships, speakers, pitches and passes are never copied. Picking a source suggests
the next edition's name/slug ("NICAR 2026" → "NICAR 2027").

**Speaker portal, part A** (`/portal/`, `apps.portal`): login by emailed one-time code (allauth
`ACCOUNT_LOGIN_BY_CODE_ENABLED`; signup password optional, staff passwords unchanged) or
Google; `/` routes staff to `/plan/` and everyone else to `/portal/`. On every login the user
is linked to unclaimed speaker profiles by **verified** email and duplicates are merged into
the most recent (`speakers.merging`: links moved, blanks filled, unique collisions absorbed,
speaker revocations win). Pages: overview with the public card, profile edit (public vs
private fields, headshot, links), sharing (grant/turn off per org). Imported profiles have no
email, so they're only claimable once staff add one (name-based claiming: open question).
**Part B** (`/portal/sessions/`): each session role with event-local time/room (only once the
schedule is published), co-speakers, confirm ("only confirmed speakers are published";
existing/imported rows were confirmed by migration), withdraw (always allowed; removes the
role), edit title/summary/description until `Event.speaker_edit_deadline` (blank = staff
only), and per-event speaker-info questions + recording consent (stored on `EventSpeaker`).
Staff: **Speaker updates** page (before/after diffs, mark reviewed, set the deadline, list of
unconfirmed speakers) and "mark confirmed" in the grid's session panel.
Email: one Google Workspace account via SMTP + app password (`EMAIL_HOST_USER`,
`EMAIL_HOST_PASSWORD`; console when unset); site name "Event Planner" in emails.
**Part C**: public **Open calls** (`/portal/calls/`, events with an open pitch window, not
private); pitch to one or several events with each event's questions and co-speakers (by
name + email; found or created, never auto-shared); **Pitches** with the speaker-visible
status (`pitches.services.speaker_status`: "Under review" until finalized; "Combined with a
similar pitch" once the pitch it joined is decided). The submitter edits while every target's
**pitch window** is open and nothing is decided (separate from the session
`speaker_edit_deadline`), withdraws per event before a decision, and confirms acceptances;
co-speakers get a read-only view once they have an account.

**Company portal, part (a)** (`apps.portal.company_views`): one portal for everyone at
`/portal/`; the menu shows each company the user is a contact for, next to speaking. Staff add
a `CompanyContact` (name + email); the person logs in with that verified email and is linked
(`commerce.claiming.claim_contacts`, also on demand). Contacts with `can_manage` edit the
shared company profile (name, website, description, logo) and add/remove colleagues, grant
or revoke manage rights, set the primary contact (the last manager can't leave). A person can
be a speaker and a contact for several companies at once.

**Company portal, part (b)** (`commerce.portal`): each company sees its sponsorships and
exhibitor bookings by event (prospect deals and draft invoices hidden; status shown as
Confirmed / Invoiced / Paid / Canceled), level benefits, amounts and sent invoices with the
payment link. Managing contacts upload deliverables (→ "received") and answer the event's
exhibitor questions (`FormQuestion.Form.EXHIBITOR`, stored on `ExhibitorBooking.answers`)
until `Event.company_edit_deadline` (blank: the event's start). Comp tickets are deferred.

**Company portal, part (c)** (staff, `planner.company_views`): per-event **Sponsors &
exhibitors** page: sponsorships with their deliverables (request with due date and
instructions, download, mark done/reopen), exhibitors with answered-question counts and a CSV
of answers, contacts per company with portal status (not invited / invited / using the
portal), add contact, send or resend invites, "invite everyone not yet invited", and the
company deadline. Invites (`commerce.invites`) are emailed from the Workspace account with a
sign-in-by-code link. Staff act only on companies with a deal at that event.

**Virtual events and attendee links:**
- **Rooms panel** (grid toolbar → Rooms): add/rename/reorder/hide/remove the event's
  columns. An org's virtual rooms (one per Zoom user, e.g. Zoom Room 0–5 + Zoom Webinar)
  live on a *private* virtual venue (`Venue.owner`) and are reused every year ("Add all").
  When an event has no rooms the grid says so instead of rendering nothing.
- **Fill rooms** (`program.scheduling.fill_rooms`): timed sessions without a room go into
  the first free room (webinar rooms last), via `place_session`; speaker clashes are listed
  and skipped; "Undo fill" reverses it.
- **Join links are never published.** `live_url` is internal-only in bundles. Attendees and
  speakers get a private **pass** (`apps.attendees`): `/my/<token>/` (schedule in the
  old join-link doc's layout, AP-style times + the visitor's local time), a personal ICS
  feed, and Join buttons that redirect via `/my/<token>/join/<public_id>/` to the
  session's *current* link and log the click. Canceled registrations and revoked passes stop
  working; "new link" reissues one person's. Staff page: **Attendee links** (issue, CSV for
  mail merge, revoke/reissue, per-person joins and device counts).
- **Run of show** (staff): every session's room, Zoom host, join link and whether it's an
  own meeting, a hand-entered link or the room's standing link; Zoom sync controls.
- **Zoom** (`integrations/connectors/zoom`, `integrations/meetings.py`): S2S OAuth; one
  meeting or webinar per session in the room's host account (`Room.host_account`);
  `sync_meetings` creates / updates / recreates (host changed) / deletes (canceled,
  unscheduled) and stores `program.VirtualMeeting`; `pull_attendance` stores joins and
  unique-participant counts only. Tested against faked HTTP; **not yet run against a live
  Zoom account.**
- Join link precedence (`program.models.join_url_for`): own meeting → `Session.live_url`
  (hand-entered) → room's standing link.

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
3. ~~**Pitch review dashboard**~~ (done). Follow-ups: pitcher notifications (deferred by
   choice), a "speaker ideas" pool view, comparing duplicate pitches across events.
   Originally planned as: **Pitch review dashboard**: filters, per-staffer votes, queue/inform buttons (the admin
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

## Requested, not started

- **Zoom go-live checklist** (needs the account Owner): create a Server-to-Server OAuth app
  with the scopes listed in `connectors/zoom/client.py`; add an org IntegrationConfig
  (connector `zoom`) and use **Save and test connection** on its admin page; set each Zoom
  room's host email in the Rooms panel; on Run of show, **Test Zoom setup** (checks each
  host is a licensed user, webinar rooms have webinars, and optionally creates + deletes a
  throwaway meeting); turn Zoom **on for the event** (off by default; ended sessions are
  never changed); run "Create / update meetings" on a test event and check one meeting in Zoom (passcode/waiting room come from
  the account defaults unless `options.meeting_settings` overrides them).
- **Email from the app** (attendee links now go out by CSV + mail merge): send passes on
  issue and automatically to people who register later; needs a mail provider.
- **AccessFest 2026 room history**: the dev DB has AF26's Zoom rooms but sessions were
  placed by "Fill rooms", not their real rooms. The real layout can be recovered by
  grouping the join-link PDF's links by meeting ID (only room labels stored).

- **Hotel share link + hotel change tracking** (requested 2026-11-13). A read-only,
  revocable link (no login) for hotel staff. From a per-event cutoff (a few days before the
  conference), changes to scheduled session titles are recorded and float to the top of the
  hotel's view, since the hotel retypes titles into its board readers by hand. An org admin
  confirms each change was made and marks it done (no integration with board readers is
  possible). Draft design, awaiting answers:
  - also track cancellations, room/time moves and late additions?
  - consolidate repeated edits of one session into a single pending row;
  - who can mark a change done;
  - link expiry.

  The hotel spec sheet ("For Hotel" orders) is the natural second section of the same link.
- **Speaker reconciliation** (dedupe/merge across events): deferred by choice; design notes are
  in the findings below.

## Findings from importing real data (NICAR 2026, AccessFest 2026)

- **Speaker duplicates across events.** The rescue files give the same person a different
  ID per conference: 20 people appear in both files and became 2 profiles each. A merge
  service + "likely duplicates" report/admin action is now the top data-quality need
  (`SpeakerProfile.merged_into` is already modeled).
- **Real schedules contain "conflicts" that were intentional.** NICAR 2026 had a speaker on
  an all-day pre-registration class (PyCAR) and a panel at the same time, a speaker on two
  sessions at once, and the welcome reception overlapping game night in one space. Hard
  blocking is right for new planning, but staff likely need an "acknowledge / allow this
  conflict" override with a note.
- **Legacy data marks cancellations in titles** ("CANCELLED - …"); the converter handles it.
- **Virtual events had no room data** (AccessFest): its sessions import unscheduled; add the
  Zoom rooms in the Rooms panel and use Fill rooms.
- **AccessFest source data attributes one speaker (ID `cffe2e51`) to 11 sessions** in the
  rescue file; the PDF shows other speakers on most of them. Fix upstream in
  conference-schedule-rescue, then re-import (Fill rooms flags it as speaker clashes).
- **Non-room locations** ("Near the escalator near White River E") currently become rooms;
  they may fit better as logistics locations.

## Known gaps and TODOs

| Area | Gap |
|---|---|
| MemberSuite | Registration address columns are unverified against a live tenant (see connector docstring). No write-back. |
| MemberClicks | Stub with a documented plan. Vimeo can list videos but not yet attach recordings to sessions. |
| Zoom | Built, untested live (see checklist above). Meeting sync runs on demand from Run of show, not automatically after each grid move. Cloud recordings aren't pulled into Vimeo yet. |
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
- [x] Private per-attendee schedule + join links (replaces the shared join-link doc)
- [x] Zoom meeting per session + attendance counts (untested live)
- [ ] Session feedback / ratings
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
