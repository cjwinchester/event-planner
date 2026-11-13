"""Browser tests for the drag-and-drop grid (Playwright + live server).

Run with the rest of the suite; skipped when Chromium isn't installed
(`uv run playwright install chromium`). Marked `browser` so they can be
excluded with `-m "not browser"`.
"""

import os

import pytest
from django.urls import reverse

from apps.program.models import Session, TimeSlot
from conftest import at

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")  # Playwright's sync API runs an event loop

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception:
        return False
    return True


if not _chromium_available():
    pytest.skip("Playwright Chromium not installed", allow_module_level=True)


@pytest.fixture
def grid_page(live_server, page, client, world):
    client.force_login(world.staff_user)
    page.context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )

    def open_grid(day="2027-03-04"):
        page.goto(f"{live_server.url}{reverse('planner:grid', args=[world.org.slug, world.event.slug])}#{day}")
        page.wait_for_selector(".grid-col")
        return page

    return open_grid


def drop_point(page, room, day_minutes):
    col = page.locator(f'.grid-col[data-room-id="{room.pk}"]')
    box = col.bounding_box()
    r0 = float(col.get_attribute("data-range-start"))
    ppm = float(col.get_attribute("data-ppm"))
    return box["x"] + box["width"] / 2, box["y"] + (day_minutes - r0) * ppm + 4


def drag(page, source, x, y):
    box = source.bounding_box()
    page.mouse.move(box["x"] + 20, box["y"] + 8)
    page.mouse.down()
    page.mouse.move(box["x"] + 40, box["y"] + 30, steps=4)
    page.mouse.move(x, y, steps=12)
    page.mouse.up()


def test_drag_from_pool_snaps_to_slot_and_saves(grid_page, world, make_session):
    TimeSlot.objects.create(event=world.event, starts_at=at(4, 10), ends_at=at(4, 11), label="Block 1")
    make_session("Pool talk")
    page = grid_page()
    x, y = drop_point(page, world.er_b, 10 * 60 + 20)
    drag(page, page.locator(".pool-item", has_text="Pool talk"), x, y)
    page.wait_for_selector('.card:has-text("Pool talk")')
    s = Session.objects.get(title="Pool talk")
    assert s.room == world.er_b and s.starts_at == at(4, 10) and s.ends_at == at(4, 11)
    if os.environ.get("GRID_SCREENSHOT"):  # e.g. GRID_SCREENSHOT=/tmp/grid.png to eyeball the layout
        page.screenshot(path=os.environ["GRID_SCREENSHOT"], full_page=True)


def test_conflicting_drop_is_rejected_with_message(grid_page, world, make_session):
    make_session("Booked", room=world.er_a, start=at(4, 10), end=at(4, 11))
    make_session("Mover")
    page = grid_page()
    x, y = drop_point(page, world.er_ab, 10 * 60 + 10)  # combined room overlaps Plaza A
    drag(page, page.locator(".pool-item", has_text="Mover"), x, y)
    page.wait_for_selector(".toast.error")
    assert "Room is already booked" in page.locator(".toast").inner_text()
    assert Session.objects.get(title="Mover").room is None


def test_drag_card_back_to_pool_unschedules(grid_page, world, make_session):
    make_session("Placed", room=world.er_a, start=at(4, 10), end=at(4, 11))
    page = grid_page()
    pool = page.locator("#pool-list").bounding_box()
    drag(page, page.locator(".card", has_text="Placed"), pool["x"] + 50, pool["y"] + 50)
    page.wait_for_selector('.pool-item:has-text("Placed")')
    assert Session.objects.get(title="Placed").room is None


def test_click_opens_panel_and_edit_refreshes_grid(grid_page, world, make_session):
    make_session("Panel me", room=world.er_a, start=at(4, 10), end=at(4, 11))
    page = grid_page()
    page.locator(".card", has_text="Panel me").click()
    page.wait_for_selector("#session-panel")
    page.fill('#session-panel input[name="title"]', "Panel edited")
    page.locator("#session-panel form").nth(1).locator("button.btn").click()
    page.wait_for_selector('.card:has-text("Panel edited")')


def test_time_slot_panel_adds_slot(grid_page, world):
    page = grid_page()
    page.click('[data-action="slots"]')
    page.wait_for_selector("#slots-panel")
    form = page.locator("#slots-panel form").nth(1)
    form.locator('input[name="start"]').fill("13:00")
    form.locator('input[name="end"]').fill("14:00")
    form.locator('input[name="label"]').fill("After lunch")
    form.locator("button").click()
    page.wait_for_selector('.slot-band:has-text("After lunch")')
    assert TimeSlot.objects.get().label == "After lunch"
