/*
 * Schedule grid — rooms across, time down, one event day per tab.
 *
 * Interaction model borrowed from FullCalendar's resource time grid, built
 * on pointer events (not HTML5 drag-and-drop) so moving and resizing share
 * one code path:
 *   - drag a card to another room/time; drag its bottom edge to resize;
 *   - drag from the Unscheduled pool onto the grid (snaps to the time slot
 *     under the pointer); drag a card back to the pool to unschedule;
 *   - while dragging, a preview shows the target and any conflicts we can
 *     see locally (room atoms incl. combined rooms, shared speakers);
 *   - on drop, the server decides (POST .../place/). 409 = not saved, the
 *     card snaps back and the reasons are shown.
 * Data: GET /api/v1/events/{id}/grid/ (see apps/program/grid.py). Times
 * from the API are UTC; everything on screen is in the event's timezone,
 * converted with Intl (DST-safe).
 */

const cfg = JSON.parse(document.getElementById("planner-config").textContent);
const TZ = cfg.timezone;
const SNAP = 15;
const ZOOMS = [0.8, 1.2, 1.6, 2.2, 3];

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const pad = (n) => String(n).padStart(2, "0");
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const state = {
  data: null,
  day: null,
  zoom: 2,
  drag: null,
  lastMove: null,
  pending: false,
  filters: { q: "", track: "", type: "", mine: false },
};

// ------------------------------------------------------------------ time

const fmt = new Intl.DateTimeFormat("en-US", {
  timeZone: TZ, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
});

function parts(date) {
  const o = {};
  for (const p of fmt.formatToParts(date)) o[p.type] = p.value;
  return { y: +o.year, m: +o.month, d: +o.day, h: +o.hour % 24, mi: +o.minute };
}
const dayOf = (date) => { const p = parts(date); return `${p.y}-${pad(p.m)}-${pad(p.d)}`; };
const minutesOf = (date) => { const p = parts(date); return p.h * 60 + p.mi; };
function offsetMinutes(date) {
  const p = parts(date);
  return (Date.UTC(p.y, p.m - 1, p.d, p.h, p.mi) - Math.floor(date.getTime() / 60000) * 60000) / 60000;
}
/** Event-local wall-clock (day "YYYY-MM-DD", minutes after midnight) → UTC Date. */
function localToUtc(day, minutes) {
  const [y, m, d] = day.split("-").map(Number);
  const guess = Date.UTC(y, m - 1, d, Math.floor(minutes / 60), minutes % 60);
  const off = offsetMinutes(new Date(guess));
  let t = guess - off * 60000;
  const off2 = offsetMinutes(new Date(t));
  if (off2 !== off) t = guess - off2 * 60000;
  return new Date(t);
}
function clock(min) {
  let h = Math.floor(min / 60) % 24;
  const m = min % 60;
  const ap = h >= 12 ? "pm" : "am";
  h = h % 12 || 12;
  return m ? `${h}:${pad(m)}${ap}` : `${h}${ap}`;
}
const dayLabel = (day) =>
  new Date(`${day}T12:00:00Z`).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
const snap = (min) => Math.round(min / SNAP) * SNAP;

// ------------------------------------------------------------------ data

const keyOf = (it) => `${it.type}:${it.id}`;

function normalize(data) {
  const withTimes = (o) => {
    if (o.starts_at && o.ends_at) {
      const s = new Date(o.starts_at);
      const e = new Date(o.ends_at);
      o._day = dayOf(s);
      o._s = minutesOf(s);
      o._e = o._s + Math.round((e - s) / 60000);
    } else {
      o._day = o._s = o._e = null;
    }
    return o;
  };
  data.items.forEach(withTimes);
  data.unscheduled.forEach(withTimes);
  data.time_slots.forEach(withTimes);
  data.roomById = Object.fromEntries(data.rooms.map((r) => [r.id, r]));
  data.trackBySlug = Object.fromEntries(data.tracks.map((t) => [t.slug, t]));
  data.errorKeys = new Set();
  data.warnKeys = new Set();
  for (const c of data.conflicts) {
    for (const ref of c.items) (c.severity === "error" ? data.errorKeys : data.warnKeys).add(`${ref.type}:${ref.id}`);
  }
  return data;
}

function findItem(type, id) {
  const all = [...state.data.items, ...state.data.unscheduled];
  return all.find((i) => i.type === type && i.id === id);
}

function csrf() {
  const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
  if (m) return decodeURIComponent(m[1]);
  try { return JSON.parse(document.body.getAttribute("hx-headers"))["X-CSRFToken"]; } catch { return ""; }
}

async function load() {
  const resp = await fetch(cfg.urls.grid, { credentials: "same-origin", headers: { Accept: "application/json" } });
  if (!resp.ok) { toast(`Couldn't load the grid (${resp.status}).`, "error"); return; }
  state.data = normalize(await resp.json());
  if (!state.day || !state.data.event.days.includes(state.day)) {
    const fromHash = location.hash.slice(1);
    state.day = state.data.event.days.includes(fromHash) ? fromHash : state.data.event.days[0];
  }
  $("#stale-banner").hidden = true;
  render();
}

// ------------------------------------------------------------------ render

function render() {
  if (!state.data) return;
  renderTabs();
  renderFilters();
  renderPool();
  renderGrid();
  const errors = state.data.conflicts.filter((c) => c.severity === "error").length;
  $("#conflict-count").textContent = errors ? String(errors) : "";
  $("#grid-loading").hidden = true;
  $("#grid").hidden = false;
}

function renderTabs() {
  const tabs = $("#day-tabs");
  tabs.innerHTML = "";
  for (const day of state.data.event.days) {
    const n = state.data.items.filter((i) => i._day === day).length;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "day-tab";
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(day === state.day));
    b.dataset.day = day;
    b.innerHTML = `${esc(dayLabel(day))}<span class="count">${n}</span>`;
    b.addEventListener("click", () => { state.day = day; history.replaceState(null, "", `#${day}`); render(); });
    tabs.appendChild(b);
  }
}

function renderFilters() {
  const fill = (select, values) => {
    const current = select.value;
    const first = select.options[0].outerHTML;
    select.innerHTML = first + values.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
    select.value = values.some(([v]) => v === current) ? current : "";
  };
  fill($("#pool-track"), state.data.tracks.map((t) => [t.slug, t.name]));
  const types = [...new Set(state.data.unscheduled.map((i) => i.session_type || i.category).filter(Boolean))].sort();
  fill($("#pool-type"), types.map((t) => [t, t]));
}

function accent(it) {
  const first = it.tracks && it.tracks[0] && state.data.trackBySlug[it.tracks[0]];
  return (first && first.color) || it.type_color || (it.type === "logistics" ? "#94a3b8" : "#64748b");
}

function speakerText(it) {
  const names = (it.speakers || []).map((s) => s.name);
  return names.length > 2 ? `${names.slice(0, 2).join(", ")} +${names.length - 2}` : names.join(", ");
}

function renderPool() {
  const f = state.filters;
  const q = f.q.toLowerCase();
  const list = $("#pool-list");
  const items = state.data.unscheduled.filter((it) => {
    if (q && !(`${it.title} ${speakerText(it)}`.toLowerCase().includes(q))) return false;
    if (f.track && !(it.tracks || []).includes(f.track)) return false;
    if (f.type && (it.session_type || it.category) !== f.type) return false;
    if (f.mine && it.owner !== cfg.userId) return false;
    return true;
  });
  $("#pool-count").textContent = `(${items.length})`;
  list.innerHTML = "";
  for (const it of items) {
    const li = document.createElement("li");
    li.className = "pool-item";
    li.dataset.key = keyOf(it);
    li.style.setProperty("--accent", accent(it));
    li.innerHTML =
      `<div class="title">${esc(it.title)}</div>` +
      `<div class="meta">${esc(it.session_type || it.category || "")}${it.speakers?.length ? " · " + esc(speakerText(it)) : ""}</div>`;
    li.addEventListener("pointerdown", (e) => startDrag(e, it, "move"));
    list.appendChild(li);
  }
  if (!items.length) list.innerHTML = `<li class="text-sm text-gray-500 p-2">Nothing here.</li>`;
}

function dayRange(items, slots) {
  let lo = 8 * 60;
  let hi = 18 * 60;
  for (const o of [...items, ...slots]) { lo = Math.min(lo, o._s); hi = Math.max(hi, o._e); }
  return [Math.floor(lo / 60) * 60, Math.min(Math.ceil(hi / 60) * 60, 24 * 60)];
}

function layoutLanes(items) {
  items.sort((a, b) => a._s - b._s || b._e - a._e);
  let cluster = [];
  let clusterEnd = -1;
  const flush = () => {
    const lanes = [];
    for (const it of cluster) {
      let lane = lanes.findIndex((end) => end <= it._s);
      if (lane < 0) { lane = lanes.length; lanes.push(0); }
      lanes[lane] = it._e;
      it._lane = lane;
    }
    for (const it of cluster) it._lanes = lanes.length;
    cluster = [];
  };
  for (const it of items) {
    if (cluster.length && it._s >= clusterEnd) flush();
    cluster.push(it);
    clusterEnd = Math.max(clusterEnd, it._e);
  }
  if (cluster.length) flush();
}

const overlaps = (aS, aE, bS, bE) => aS < bE && bS < aE;
const sharesAtom = (a, b) => a && b && a.atoms.some((x) => b.atoms.includes(x));

function renderGrid() {
  const { rooms } = state.data;
  const ppm = ZOOMS[state.zoom];
  const dayItems = state.data.items.filter((i) => i._day === state.day && state.data.roomById[i.room]);
  const slots = state.data.time_slots.filter((s) => s._day === state.day);
  const [r0, r1] = dayRange(dayItems, slots);
  state.range = [r0, r1];
  const height = (r1 - r0) * ppm;
  const y = (min) => (min - r0) * ppm;

  const grid = $("#grid");
  grid.style.gridTemplateColumns = `64px repeat(${rooms.length}, minmax(160px, 1fr))`;
  grid.innerHTML = "";
  grid.insertAdjacentHTML("beforeend", `<div class="grid-corner">${esc(TZ.split("/").pop().replace("_", " "))}</div>`);
  for (const r of rooms) {
    const info = [r.setup, r.capacity ? `cap ${r.capacity}` : "", r.computer_os ? r.computer_os.toUpperCase() : "", r.is_recorded ? "rec" : ""]
      .filter(Boolean).join(" · ");
    grid.insertAdjacentHTML(
      "beforeend",
      `<div class="grid-room-head" data-room-id="${r.id}"><span class="name">${esc(r.name)}</span><span class="info">${esc(info)}</span></div>`,
    );
  }

  const axis = document.createElement("div");
  axis.className = "grid-axis";
  axis.style.height = `${height}px`;
  for (let m = r0; m <= r1; m += 60) {
    axis.insertAdjacentHTML("beforeend", `<div class="hour-label" style="top:${y(m)}px">${clock(m)}</div>`);
  }
  for (const s of slots) {
    axis.insertAdjacentHTML("beforeend", `<div class="axis-slot" style="top:${y(s._s)}px;height:${(s._e - s._s) * ppm}px" title="${esc(s.label)}"></div>`);
  }
  grid.appendChild(axis);

  rooms.forEach((room, idx) => {
    const col = document.createElement("div");
    col.className = "grid-col";
    col.dataset.roomId = room.id;
    col.dataset.rangeStart = r0;
    col.dataset.ppm = ppm;
    col.style.height = `${height}px`;
    for (let m = r0; m < r1; m += 30) {
      col.insertAdjacentHTML("beforeend", `<div class="hour-line${m % 60 ? " half" : ""}" style="top:${y(m)}px"></div>`);
    }
    for (const s of slots) {
      const label = idx === 0 && s.label ? `<span class="label">${esc(s.label)}</span>` : "";
      col.insertAdjacentHTML("beforeend", `<div class="slot-band" style="top:${y(s._s)}px;height:${(s._e - s._s) * ppm}px">${label}</div>`);
    }
    // Ghosts: blocking items in *other* rooms that share an atom (combined rooms).
    for (const it of dayItems) {
      if (!it.blocks_room || it.room === room.id) continue;
      const other = state.data.roomById[it.room];
      if (sharesAtom(room, other)) {
        col.insertAdjacentHTML(
          "beforeend",
          `<div class="ghost-block" style="top:${y(it._s)}px;height:${(it._e - it._s) * ppm}px">Blocked by ${esc(it.title)} (${esc(other.name)})</div>`,
        );
      }
    }
    const here = dayItems.filter((i) => i.room === room.id);
    const blocking = here.filter((i) => i.blocks_room);
    layoutLanes(blocking);
    for (const it of blocking) col.appendChild(cardEl(it, y, ppm));
    for (const it of here.filter((i) => !i.blocks_room)) {
      const strip = document.createElement("div");
      strip.className = "service-strip";
      strip.style.top = `${y(it._s)}px`;
      strip.style.height = `${(it._e - it._s) * ppm}px`;
      strip.title = `${it.title} ${clock(it._s)}–${clock(it._e)} (shares the room)`;
      strip.dataset.key = keyOf(it);
      strip.addEventListener("pointerdown", (e) => startDrag(e, it, "move"));
      col.appendChild(strip);
    }
    grid.appendChild(col);
  });
}

function cardEl(it, y, ppm) {
  const el = document.createElement("div");
  const key = keyOf(it);
  el.className = "card";
  if (it.type === "logistics") el.classList.add("logistics");
  if (it.type === "session" && !it.is_ready) el.classList.add("not-ready");
  if (state.data.errorKeys.has(key)) el.classList.add("conflict");
  else if (state.data.warnKeys.has(key)) el.classList.add("warning");
  if (!cfg.canEdit) el.classList.add("readonly");
  el.dataset.key = key;
  el.style.setProperty("--accent", accent(it));
  el.style.top = `${y(it._s)}px`;
  el.style.height = `${Math.max((it._e - it._s) * ppm - 1, 14)}px`;
  el.style.left = `calc(${(it._lane / it._lanes) * 100}% + 2px)`;
  el.style.width = `calc(${100 / it._lanes}% - 4px)`;
  const badges = [
    it.type === "session" && it.is_ready ? '<span title="Ready to publish">✓</span>' : "",
    it.is_plenary ? '<span title="Plenary">★</span>' : "",
    state.data.errorKeys.has(key) ? '<span title="Conflict">⚠</span>' : "",
  ].join("");
  const who = speakerText(it);
  el.title = `${it.title}\n${clock(it._s)}–${clock(it._e)}${who ? "\n" + who : ""}`;
  el.innerHTML =
    `<div class="badges">${badges}</div><div class="title">${esc(it.title)}</div>` +
    `<div class="meta">${clock(it._s)}–${clock(it._e)}${who ? " · " + esc(who) : it.category ? " · " + esc(it.category) : ""}</div>` +
    (cfg.canEdit ? '<div class="resize" aria-hidden="true"></div>' : "");
  el.addEventListener("pointerdown", (e) => startDrag(e, it, e.target.classList.contains("resize") ? "resize" : "move"));
  return el;
}

// ------------------------------------------------------------------ local conflict preview

function localProblems(it, roomId, s, e) {
  const room = state.data.roomById[roomId];
  const problems = [];
  if (it.blocks_room) {
    for (const o of state.data.items) {
      if (o === it || o._day !== state.day || !o.blocks_room) continue;
      if (overlaps(s, e, o._s, o._e) && sharesAtom(room, state.data.roomById[o.room])) {
        problems.push(`Room booked: ${o.title}`);
      }
    }
  }
  const mine = new Set((it.speakers || []).map((x) => x.id));
  if (mine.size) {
    for (const o of state.data.items) {
      if (o === it || o.type !== "session" || o._day !== state.day || !overlaps(s, e, o._s, o._e)) continue;
      for (const sp of o.speakers || []) if (mine.has(sp.id)) problems.push(`${sp.name} is on ${o.title}`);
    }
  }
  return problems;
}

// ------------------------------------------------------------------ drag & resize

function startDrag(e, it, mode) {
  if (e.button !== 0) return;
  const src = e.currentTarget;
  state.drag = { it, mode, src, x0: e.clientX, y0: e.clientY, started: false, target: null, ghost: null };
  const rect = src.getBoundingClientRect();
  state.drag.offsetY = src.classList.contains("card") ? e.clientY - rect.top : 8;
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp, { once: true });
}

function onMove(e) {
  const d = state.drag;
  if (!d) return;
  if (!d.started) {
    if (Math.hypot(e.clientX - d.x0, e.clientY - d.y0) < 4) return;
    if (!cfg.canEdit) return;
    d.started = true;
    d.src.classList.add("dragging");
    if (d.mode === "move") {
      d.ghost = d.src.cloneNode(true);
      d.ghost.classList.add("drag-ghost");
      d.ghost.classList.remove("dragging");
      const r = d.src.getBoundingClientRect();
      d.ghost.style.width = `${Math.max(r.width, 160)}px`;
      d.ghost.style.height = `${Math.min(r.height, 80)}px`;
      document.body.appendChild(d.ghost);
    }
  }
  if (d.ghost) {
    d.ghost.style.left = `${e.clientX + 10}px`;
    d.ghost.style.top = `${e.clientY - d.offsetY}px`;
  }
  d.target = d.mode === "move" ? targetForMove(e, d) : targetForResize(e, d);
  drawPreview(d.target);
}

function columnAt(x, y) {
  return $$(".grid-col").find((c) => {
    const r = c.getBoundingClientRect();
    return x >= r.left && x < r.right && y >= r.top && y < r.bottom;
  });
}

function targetForMove(e, d) {
  const pool = $("#pool").getBoundingClientRect();
  if (e.clientX >= pool.left && e.clientX < pool.right && e.clientY >= pool.top && e.clientY < pool.bottom) {
    return d.it._s == null ? null : { kind: "pool" };
  }
  const col = columnAt(e.clientX, e.clientY);
  if (!col) return null;
  const [r0, r1] = state.range;
  const ppm = ZOOMS[state.zoom];
  const top = col.getBoundingClientRect().top;
  const roomId = Number(col.dataset.roomId);
  let s;
  let len;
  if (d.it._s == null) {
    // From the pool: snap to the time slot under the pointer, else to SNAP.
    const at = r0 + (e.clientY - top) / ppm;
    const slot = state.data.time_slots.find((t) => t._day === state.day && at >= t._s && at < t._e);
    if (slot) { s = slot._s; len = slot._e - slot._s; }
    else { len = d.it.default_minutes || 60; s = snap(at - len / 2); }
  } else {
    len = d.it._e - d.it._s;
    s = snap(r0 + (e.clientY - d.offsetY - top) / ppm);
  }
  s = Math.max(0, Math.min(s, 24 * 60 - len));
  return { kind: "cell", roomId, s, e: s + len, col, problems: localProblems(d.it, roomId, s, s + len), r0, r1 };
}

function targetForResize(e, d) {
  const col = $(`.grid-col[data-room-id="${d.it.room}"]`);
  if (!col) return null;
  const [r0] = state.range;
  const ppm = ZOOMS[state.zoom];
  const end = Math.max(d.it._s + SNAP, Math.min(24 * 60, snap(r0 + (e.clientY - col.getBoundingClientRect().top) / ppm)));
  return { kind: "cell", roomId: d.it.room, s: d.it._s, e: end, col, problems: localProblems(d.it, d.it.room, d.it._s, end), r0 };
}

function drawPreview(t) {
  $$(".preview").forEach((p) => p.remove());
  $("#pool-list").classList.toggle("drop-target", t?.kind === "pool");
  if (!t || t.kind !== "cell") return;
  const ppm = ZOOMS[state.zoom];
  const p = document.createElement("div");
  p.className = `preview${t.problems.length ? " bad" : ""}`;
  p.style.top = `${(t.s - t.r0) * ppm}px`;
  p.style.height = `${(t.e - t.s) * ppm}px`;
  p.textContent = `${clock(t.s)}–${clock(t.e)}${t.problems.length ? " · " + t.problems[0] : ""}`;
  t.col.appendChild(p);
}

function endDrag() {
  const d = state.drag;
  window.removeEventListener("pointermove", onMove);
  d?.ghost?.remove();
  d?.src?.classList.remove("dragging");
  drawPreview(null);
  state.drag = null;
}

function onUp() {
  const d = state.drag;
  if (!d) return;
  const { it, target, started } = d;
  endDrag();
  if (!started) { openItem(it); return; }
  if (!target) return;
  if (target.kind === "pool") { place(it, null, null, null); return; }
  if (target.roomId === it.room && target.s === it._s && target.e === it._e && it._day === state.day) return;
  place(it, target.roomId, localToUtc(state.day, target.s), localToUtc(state.day, target.e));
}

window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.drag) { endDrag(); return; }
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "");
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !typing) { e.preventDefault(); undo(); }
});

// ------------------------------------------------------------------ server writes

async function place(it, roomId, start, end, { recordUndo = true } = {}) {
  if (state.pending) return;
  state.pending = true;
  const url = (it.type === "session" ? cfg.urls.placeSession : cfg.urls.placeLogistics).replace("{id}", it.id);
  const prev = { room: it.room, starts_at: it.starts_at, ends_at: it.ends_at };
  try {
    const resp = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({
        room: roomId,
        starts_at: start ? start.toISOString() : null,
        ends_at: end ? end.toISOString() : null,
        dry_run: false,
      }),
    });
    const body = await resp.json().catch(() => ({}));
    if (resp.ok && body.saved) {
      if (recordUndo) {
        state.lastMove = { type: it.type, id: it.id, title: it.title, prev };
        $('[data-action="undo"]').disabled = false;
      }
      const warnings = (body.conflicts || []).filter((c) => c.severity === "warning");
      const verb = roomId ? "Moved" : "Unscheduled";
      toast(
        warnings.length ? `${verb} “${it.title}” — warning: ${warnings.map((w) => w.message).join(" ")}` : `${verb} “${it.title}”.`,
        warnings.length ? "warn" : "ok",
      );
      await load();
    } else if (resp.status === 409) {
      const errors = (body.conflicts || []).filter((c) => c.severity === "error").map((c) => c.message);
      toast(`Not moved: ${errors.join(" ")}`, "error");
      render();
    } else {
      const detail = body.detail || (Array.isArray(body) ? body.join(" ") : Object.values(body).flat().join(" "));
      toast(`Not moved: ${detail || resp.status}`, "error");
      render();
    }
  } finally {
    state.pending = false;
  }
}

function undo() {
  const m = state.lastMove;
  if (!m) return;
  const it = findItem(m.type, m.id);
  if (!it) return;
  state.lastMove = null;
  $('[data-action="undo"]').disabled = true;
  place(it, m.prev.room, m.prev.starts_at ? new Date(m.prev.starts_at) : null, m.prev.ends_at ? new Date(m.prev.ends_at) : null, { recordUndo: false });
}

// ------------------------------------------------------------------ panels

function showPanel() { $("#side-panel").hidden = false; }

function openPanel(url) {
  showPanel();
  window.htmx.ajax("GET", url, { target: "#side-panel-body", swap: "innerHTML" });
}

function openItem(it) {
  if (it.type === "session") openPanel(cfg.urls.sessionPanel.replace("{id}", it.id));
  else window.open(cfg.urls.admin.logistics.replace("{id}", it.id), "_blank", "noopener");
}

function showConflicts() {
  showPanel();
  const list = state.data.conflicts;
  $("#side-panel-body").innerHTML =
    `<div class="panel"><h2 class="panel-title">Conflicts</h2>` +
    (list.length
      ? `<ul class="panel-conflicts">${list
          .map((c, i) => `<li class="${c.severity}"><button type="button" data-conflict="${i}">${esc(c.message)}</button></li>`)
          .join("")}</ul>`
      : `<p class="panel-ok">No conflicts.</p>`) +
    `</div>`;
  $$("[data-conflict]").forEach((b) =>
    b.addEventListener("click", () => {
      const ref = list[Number(b.dataset.conflict)].items[0];
      const it = ref && findItem(ref.type, ref.id);
      if (!it?._day) return;
      state.day = it._day;
      render();
      const el = $(`.card[data-key="${keyOf(it)}"]`);
      el?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
      el?.classList.add("flash");
    }),
  );
}

// ------------------------------------------------------------------ chrome

let toastTimer;
function toast(message, kind = "ok") {
  const t = $("#toast");
  t.textContent = message;
  t.className = `toast ${kind}`;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, kind === "error" ? 9000 : 4000);
}

async function checkStale() {
  if (!state.data || state.drag || state.pending || document.hidden) return;
  try {
    const resp = await fetch(cfg.urls.version, { credentials: "same-origin" });
    if (!resp.ok) return;
    const { version } = await resp.json();
    if (version !== state.data.version) $("#stale-banner").hidden = false;
  } catch { /* offline: try again next tick */ }
}

function bindChrome() {
  document.addEventListener("click", (e) => {
    const action = e.target.closest("[data-action]")?.dataset.action;
    if (!action) return;
    if (action === "refresh") load();
    if (action === "undo") undo();
    if (action === "zoom-in") { state.zoom = Math.min(ZOOMS.length - 1, state.zoom + 1); render(); }
    if (action === "zoom-out") { state.zoom = Math.max(0, state.zoom - 1); render(); }
    if (action === "conflicts") showConflicts();
    if (action === "slots") openPanel(`${cfg.urls.slots}?day=${state.day}`);
    if (action === "new-session") openPanel(cfg.urls.newSession);
    if (action === "close-panel") $("#side-panel").hidden = true;
  });
  $("#pool-search").addEventListener("input", (e) => { state.filters.q = e.target.value; renderPool(); });
  $("#pool-track").addEventListener("change", (e) => { state.filters.track = e.target.value; renderPool(); });
  $("#pool-type").addEventListener("change", (e) => { state.filters.type = e.target.value; renderPool(); });
  $("#pool-mine").addEventListener("change", (e) => { state.filters.mine = e.target.checked; renderPool(); });
  // Panels send HX-Trigger: grid:refresh after a successful save.
  document.body.addEventListener("grid:refresh", () => load());
  setInterval(checkStale, 20000);
}

bindChrome();
load();
