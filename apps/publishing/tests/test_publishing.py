import json
from pathlib import Path

import jsonschema
import pytest
from django.core.files.storage import storages

from apps.commerce.models import Company, DealStatus, Sponsorship
from apps.publishing.bundle import Audience, build_event_bundle, validate_bundle
from apps.publishing.exporters import ExportContext, get_exporter
from apps.publishing.importer import import_bundle
from apps.publishing.services import publish_event
from apps.speakers.models import EventSpeaker
from conftest import at

FIXTURES = Path(__file__).parent / "fixtures"


def _weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401
    except OSError:  # Pango not loadable (macOS: export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib)
        return False
    return True


needs_pdf = pytest.mark.skipif(not _weasyprint_available(), reason="WeasyPrint system libraries not available")


@pytest.fixture
def scheduled(world, make_session):
    ready = make_session(
        "Scraping 101",
        room=world.er_a,
        start=at(5, 9),
        end=at(5, 10),
        speakers=[world.alice],
        description="<p>Learn <script>alert(1)</script><strong>scraping</strong></p>",
    )
    ready.tracks.set([world.track_ai])
    make_session("Draft", edited=False, room=world.er_b, start=at(5, 9), end=at(5, 10))
    make_session("Unscheduled")
    EventSpeaker.objects.create(event=world.event, speaker=world.alice, notes="SECRET-NOTE", bio_override="Edited bio")
    Sponsorship.objects.create(
        event=world.event, company=Company.objects.create(name="Acme", slug="acme"), status=DealStatus.CONTRACTED
    )
    return ready


@pytest.mark.django_db
class TestBundle:
    def test_public_bundle_is_valid_and_only_ready(self, world, scheduled):
        bundle = build_event_bundle(world.event)
        validate_bundle(bundle)
        assert [s["title"] for s in bundle["sessions"]] == ["Scraping 101"]
        assert bundle["sessions"][0]["starts_at"] == "2027-03-05T14:00:00Z"  # 9am EST in UTC
        assert "<script>" not in bundle["sessions"][0]["description"]
        assert bundle["speakers"][0]["bio"] == "Edited bio"
        assert [sp["id"] for sp in bundle["sponsors"]] == ["acme"]

    def test_public_bundle_never_contains_private_data(self, world, scheduled):
        from apps.events.models import EventContract

        EventContract.objects.create(event=world.event, title="Contract", attendee_room_rate=219)
        text = json.dumps(build_event_bundle(world.event))
        for secret in ("alice@example.com", "SECRET-NOTE", "219", "internal_notes"):
            assert secret not in text

    def test_unpublished_event_has_no_sessions(self, world, scheduled):
        world.event.publish_schedule = False
        world.event.save()
        assert build_event_bundle(world.event)["sessions"] == []

    def test_internal_bundle_includes_workflow_fields(self, world, scheduled):
        bundle = build_event_bundle(world.event, Audience.INTERNAL)
        validate_bundle(bundle)
        assert {s["title"] for s in bundle["sessions"]} == {"Scraping 101", "Draft", "Unscheduled"}
        assert all("title_status" in s for s in bundle["sessions"])
        assert "email" not in bundle["speakers"][0]  # no share grant yet


@pytest.mark.django_db
class TestExporters:
    def render(self, world, slug):
        bundle = build_event_bundle(world.event)
        return get_exporter(slug).render(bundle, ExportContext(feeds={"json": "https://x/ev.json"}, calendar_host="t"))

    def test_schedule_ui_matches_existing_ui_schema(self, world, scheduled):
        (f,) = self.render(world, "schedule_ui")
        data = json.loads(f.content)
        jsonschema.validate(data, json.loads((FIXTURES / "schedule-ui.schema.json").read_text()))
        assert data["sessions"][0]["start_time"] == "2027-03-05T14:00:00.000Z"
        assert data["sessions"][0]["speakers"] == "Alice Analyst, Daily News"

    def test_ics_has_stable_uids_and_track_feeds(self, world, scheduled):
        files = {f.path: f.content.decode() for f in self.render(world, "ics")}
        main = files["nicar-2027.ics"]
        assert f"UID:ire-nicar-2027-{scheduled.public_id}@t" in main
        assert "DTSTART:20270305T140000Z" in main
        assert "ics/track-ai.ics" in files

    def test_csv_and_txt(self, world, scheduled):
        (csv_file,) = self.render(world, "csv")
        assert csv_file.content.decode().splitlines()[0].startswith("session_id,title")
        assert "09:00" in csv_file.content.decode()  # local time
        (txt,) = self.render(world, "txt")
        assert "Scraping 101" in txt.content.decode()

    @needs_pdf
    def test_pdf(self, world, scheduled):
        (pdf,) = self.render(world, "pdf")
        assert pdf.content.startswith(b"%PDF")


@pytest.mark.django_db
class TestImporter:
    def load(self):
        return json.loads((FIXTURES / "sample-bundle.json").read_text())

    def test_import_scrubbed_fixture_and_reimport_is_idempotent(self, world):
        result = import_bundle(self.load(), world.other_org)
        event = result.event
        assert event.organization == world.other_org
        assert event.sessions.count() == 14 and event.tracks.count() == 6
        assert result.created["speakers"] == 14
        again = import_bundle(self.load(), world.other_org)
        assert again.event == event and "sessions" not in again.created
        assert event.sessions.count() == 14

    def test_dry_run_rolls_back(self, world):
        result = import_bundle(self.load(), world.other_org, dry_run=True)
        assert result.created["sessions"] == 14
        assert not world.other_org.events.exists()

    def test_invalid_bundle_rejected(self, world):
        bad = self.load()
        bad["sessions"][0]["starts_at"] = "March 5"
        with pytest.raises(jsonschema.ValidationError):
            import_bundle(bad, world.other_org)

    def test_round_trip_export_import_export(self, world, scheduled):
        exported = build_event_bundle(world.event)
        imported = import_bundle(exported, world.other_org).event
        imported.publish_schedule = True
        imported.save()
        again = build_event_bundle(imported)
        strip = lambda b: [(s["id"], s["title"], s["starts_at"], s["tracks"]) for s in b["sessions"]]  # noqa: E731
        assert strip(again) == strip(exported)


@pytest.mark.django_db
def test_publish_writes_every_format_locally(world, scheduled):
    formats = ["json", "schedule_ui", "csv", "txt", "ics"] + (["pdf"] if _weasyprint_available() else [])
    run = publish_event(world.event, formats=formats)
    assert run.status == run.Status.SUCCEEDED, run.message
    paths = {f["path"] for f in run.files}
    for name in ("nicar-2027.json", "nicar-2027-schedule.json", "nicar-2027.ics", "nicar-2027-schedule.csv"):
        assert f"ire/nicar-2027/{name}" in paths
    assert storages["publish"].exists("ire/nicar-2027/nicar-2027.json")
    legacy = json.loads(storages["publish"].open("ire/nicar-2027/nicar-2027-schedule.json").read())
    assert legacy["schedule_formats"]["json"].endswith("ire/nicar-2027/nicar-2027.json")


@pytest.mark.django_db
def test_public_feeds(client, world, scheduled):
    assert client.get("/feeds/ire/nicar-2027/bundle.json").json()["sessions"][0]["title"] == "Scraping 101"
    resp = client.get(f"/feeds/ire/nicar-2027/sessions/{scheduled.public_id}.ics")
    assert resp.status_code == 200 and b"BEGIN:VEVENT" in resp.content
    assert client.get("/feeds/ire/nicar-2027/tracks/ai.ics").status_code == 200
    world.event.publish_schedule = False
    world.event.save()
    assert client.get("/feeds/ire/nicar-2027/bundle.json").status_code == 404


@pytest.mark.django_db
def test_one_failing_format_does_not_block_others(world, scheduled, monkeypatch):
    from apps.publishing.exporters import get_exporter as real_get

    def boom(*args, **kwargs):
        raise RuntimeError("no fonts")

    monkeypatch.setattr(real_get("txt"), "render", boom)
    run = publish_event(world.event, formats=["json", "txt"])
    assert run.status == run.Status.PARTIAL and "txt" in run.message
    assert [f["format"] for f in run.files] == ["json"]
