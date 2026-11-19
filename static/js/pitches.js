/*
 * Pitch review: keyboard and selection helpers. The table is server-rendered
 * (HTMX); this adds:
 *   j / k      move the active row        Enter   open the side panel
 *   y / m / n  vote yes / maybe / no      x       toggle selection
 * plus select-all, the bulk bar's extra fields, and panel open/close.
 */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let activeId = null;

const rows = () => $$(".pitch-row");
const activeRow = () => (activeId ? $(`#pitch-row-${activeId}`) : null);

function activate(row, { scroll = true } = {}) {
  for (const r of rows()) r.classList.toggle("is-active", r === row);
  activeId = row ? row.dataset.id : null;
  if (!row) return;
  row.focus({ preventScroll: true });
  if (scroll) row.scrollIntoView({ block: "nearest" });
}

function openPanel(url) {
  $("#side-panel").hidden = false;
  window.htmx.ajax("GET", url, { target: "#side-panel-body", swap: "innerHTML" });
}

function openRow(row) {
  for (const r of rows()) r.classList.toggle("is-open", r === row);
  openPanel(row.dataset.panelUrl);
}

function updateBulk() {
  const n = $$('input[name="ids"]:checked').length;
  const counter = $("#bulk-count");
  if (counter) counter.textContent = `${n} selected`;
}

document.addEventListener("keydown", (e) => {
  if (/INPUT|TEXTAREA|SELECT/.test(e.target.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
  const list = rows();
  if (!list.length) return;
  const row = activeRow();
  const idx = list.indexOf(row);
  if (e.key === "j") {
    activate(list[Math.min(idx + 1, list.length - 1)]);
    e.preventDefault();
  } else if (e.key === "k") {
    activate(list[Math.max(idx - 1, 0)]);
    e.preventDefault();
  } else if (e.key === "Enter" && row) {
    openRow(row);
    e.preventDefault();
  } else if (e.key === "x" && row) {
    const box = row.querySelector('input[name="ids"]');
    if (box) {
      box.checked = !box.checked;
      updateBulk();
    }
  } else if (["y", "m", "n"].includes(e.key) && row) {
    const vote = { y: "yes", m: "maybe", n: "no" }[e.key];
    row.querySelector(`.vote-btn[data-vote="${vote}"]`)?.click();
    e.preventDefault();
  }
});

document.addEventListener("click", (e) => {
  if (e.target.closest('[data-action="close-panel"]')) {
    $("#side-panel").hidden = true;
    return;
  }
  const opener = e.target.closest("[data-panel-url]:not(.pitch-row)");
  if (opener) {
    openPanel(opener.dataset.panelUrl);
    return;
  }
  if (e.target.matches("[data-select-all]")) {
    for (const b of $$('input[name="ids"]')) b.checked = e.target.checked;
    updateBulk();
    return;
  }
  const row = e.target.closest(".pitch-row");
  if (row && !e.target.closest("button, input, a, select, label")) {
    activate(row, { scroll: false });
    openRow(row);
  }
});

document.addEventListener("change", (e) => {
  if (e.target.matches('input[name="ids"]')) updateBulk();
  if (e.target.matches('.bulk-bar select[name="action"]')) {
    e.target.closest(".bulk-bar").dataset.action = e.target.value;
  }
});

// Keep the active row highlighted after HTMX swaps a row (a vote) or the table.
document.body.addEventListener("htmx:afterSwap", () => {
  activeRow()?.classList.add("is-active");
  updateBulk();
});

// Panels ask the table to refresh with the current filters.
document.body.addEventListener("pitches:refresh", () => {
  window.htmx.trigger("#pitch-filters", "change");
});

// Lets browser tests (and anything else) know the shortcuts are wired up.
document.documentElement.dataset.pitchesReady = "1";
