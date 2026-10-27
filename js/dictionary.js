// The grammar dictionary panel in the bottom-right corner.
// Any element with class="term" and data-term="<id>" opens it at that entry.

import { getDictionary } from "./data.js";
import { esc } from "./ui.js";

const panel = document.getElementById("dict-panel");
const toggle = document.getElementById("dict-toggle");
const body = document.getElementById("dict-body");
const search = document.getElementById("dict-search");
let terms = [];
let lastOpener = null;

function render(filter = "", focusId = null) {
  const q = filter.trim().toLowerCase();
  const list = terms.filter(
    (t) => !q || t.term.toLowerCase().includes(q) || t.definition.toLowerCase().includes(q)
  );
  if (!list.length) {
    body.innerHTML = `<p class="muted">No terms match "${esc(filter)}".</p>`;
    return;
  }
  body.innerHTML = list
    .map(
      (t) => `
      <article class="dict-entry${t.id === focusId ? " focus" : ""}" id="dict-${t.id}">
        <h3>${esc(t.term)} <span class="kind">${esc(t.kind)}</span></h3>
        <p>${esc(t.definition)}</p>
        ${t.asl ? `<p class="asl"><strong>In ASL:</strong> ${esc(t.asl)}</p>` : ""}
      </article>`
    )
    .join("");
  if (focusId) {
    const el = document.getElementById(`dict-${focusId}`);
    if (el) el.scrollIntoView({ block: "start" });
  }
}

export function openDictionary(termId = null) {
  panel.hidden = false;
  toggle.hidden = true;
  toggle.setAttribute("aria-expanded", "true");
  search.value = "";
  render("", termId);
  if (!termId) search.focus();
  else {
    const h = panel.querySelector(".dict-entry.focus h3");
    if (h) {
      h.setAttribute("tabindex", "-1");
      h.focus({ preventScroll: true });
    }
  }
}

export function closeDictionary() {
  panel.hidden = true;
  toggle.hidden = false;
  toggle.setAttribute("aria-expanded", "false");
  (lastOpener || toggle).focus();
}

export async function initDictionary() {
  terms = await getDictionary();
  terms.sort((a, b) => a.term.localeCompare(b.term));

  toggle.addEventListener("click", () => {
    lastOpener = toggle;
    openDictionary();
  });
  document.getElementById("dict-close").addEventListener("click", closeDictionary);
  search.addEventListener("input", () => render(search.value));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden && !document.querySelector("dialog[open]")) closeDictionary();
  });

  // Clicking any highlighted grammar word anywhere on the page
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".term");
    if (!btn) return;
    lastOpener = btn;
    openDictionary(btn.dataset.term);
  });
}
