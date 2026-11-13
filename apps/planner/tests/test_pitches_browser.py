"""Browser tests for the pitch review dashboard (Playwright + live server)."""

import os

import pytest
from django.urls import reverse

from apps.organizations.models import Membership
from apps.pitches import services
from apps.pitches.models import PitchTarget

from .test_grid_browser import _chromium_available

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

if not _chromium_available():
    pytest.skip("Playwright Chromium not installed", allow_module_level=True)


@pytest.fixture
def dashboard(live_server, page, client, world, django_user_model):
    colleague = django_user_model.objects.create_user("colleague@ire.org", "pw", first_name="Col", last_name="League")
    Membership.objects.create(user=colleague, organization=world.org, role=Membership.Role.STAFF)
    for title, speaker in (("Scraping 101", world.alice), ("FOIA tactics", world.bob)):
        p = services.submit_pitch(submitter=speaker, title=title, description="D", events=[world.event], on_behalf=True)
        services.cast_vote(p.targets.get(), colleague, "yes")

    def open_as(user):
        client.force_login(user)
        page.context.clear_cookies()
        page.context.add_cookies(
            [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
        )
        page.goto(f"{live_server.url}{reverse('planner:pitches', args=[world.org.slug, world.event.slug])}")
        page.wait_for_selector(".pitch-row")
        return page

    return open_as


def test_keyboard_vote_reveals_tally_then_bulk_queue(dashboard, world):
    page = dashboard(world.staff_user)
    first = page.locator(".pitch-row").first
    title = first.locator(".pitch-title").inner_text()
    assert "vote to see" in first.inner_text()

    page.locator("body").press("j")
    page.locator("body").press("y")
    page.wait_for_selector(".pitch-row.is-active .vote-btn.yes.chosen")
    row = page.locator(".pitch-row", has_text=title)
    assert "vote to see" not in row.inner_text() and "CL" in row.inner_text()  # colleague's initials

    page.locator("body").press("x")
    page.select_option('.bulk-bar select[name="action"]', "queue_accept")
    page.click(".bulk-bar button")
    page.wait_for_selector(".panel-ok")
    assert PitchTarget.objects.get(pitch__title=title).status == PitchTarget.Status.ACCEPT_QUEUE
    if os.environ.get("PITCH_SCREENSHOT"):  # e.g. PITCH_SCREENSHOT=/tmp/pitches.png
        page.set_viewport_size({"width": 1400, "height": 800})
        page.locator(".pitch-row", has_text=title).locator(".pitch-title").click()
        page.wait_for_selector("#pitch-panel")
        page.screenshot(path=os.environ["PITCH_SCREENSHOT"])


def test_admin_finalizes(dashboard, world):
    target = PitchTarget.objects.get(pitch__title="Scraping 101")
    services.queue_decision([target], PitchTarget.Status.ACCEPT_QUEUE, world.admin_user)
    page = dashboard(world.admin_user)
    page.click("text=Review queued decisions")
    page.click("text=Finalize 1 decision")
    page.wait_for_selector("text=1 accepted")
    target.refresh_from_db()
    assert target.status == PitchTarget.Status.ACCEPTED and target.session_id
