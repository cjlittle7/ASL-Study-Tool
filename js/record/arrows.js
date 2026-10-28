// Arrows on a recorded GIF: the editor in the Review panel.
// Blender keeps the arrows with the take; this page edits them and shows a preview picture
// Blender renders after each change. Every arrow is a small object (see TAKE_ARROW_DEFAULTS in
// the add-on); only the fields that change are sent.

import { esc } from "../ui.js";

const PARTS = [
  ["index_tip", "Index fingertip"], ["middle_tip", "Middle fingertip"], ["ring_tip", "Ring fingertip"],
  ["pinky_tip", "Pinky fingertip"], ["thumb_tip", "Thumb tip"], ["palm", "Palm"], ["wrist", "Wrist"],
  ["forearm", "Forearm"], ["elbow", "Elbow"], ["shoulder", "Shoulder"], ["head", "Forehead"], ["chin", "Chin"],
];
const KINDS = [
  ["path", "Path of the movement"], ["along", "Straight, on a body part"], ["around", "Circling a body part"],
  ["straight", "Straight (fixed)"], ["arc", "Arc (fixed)"], ["circle", "Circle (fixed)"],
  ["double", "Back and forth (fixed)"], ["zigzag", "Zigzag (fixed)"],
];
const AXES = [["BONE", "Along the bone"], ["BEND", "Front / palm side"], ["SIDE", "Sideways"]];

const FIXED = new Set(["straight", "arc", "circle", "double", "zigzag"]);
const kindName = (k) => (KINDS.find((x) => x[0] === k) || [k, k])[1];

// field: [key, label, min, max, step, unit]
const POSITION = [["x", "Left / right", -200, 200, 1, "mm"], ["y", "Down / up", -200, 200, 1, "mm"],
  ["z", "Back / front", -200, 200, 1, "mm"]];
const PATH_FIELDS = [["trim_start", "Cut from the start", 0, 45, 1, "%"], ["trim_end", "Cut from the end", 0, 45, 1, "%"],
  ["smooth", "Smoothing", 0, 100, 5, "%"]];
const TETHER_FIELDS = {
  along: [["length", "Length", 10, 400, 5, "mm"], ["spin", "Turn around the axis", -180, 180, 5, "°"],
    ["tilt", "Tilt", -90, 90, 5, "°"]],
  around: [["radius", "Radius", 10, 200, 1, "mm"], ["sweep", "How far around", 30, 350, 5, "°"],
    ["spin", "Turn around the axis", -180, 180, 5, "°"], ["tilt", "Tilt", -90, 90, 5, "°"]],
};
const FIXED_FIELDS = [["size", "Size", 20, 300, 5, "%"], ["rotate", "Turn", -180, 180, 5, "°"]];
const STYLE_FIELDS = [["thickness", "Thickness", 30, 300, 5, "%"]];

/**
 * @param {HTMLElement} el      where the editor lives
 * @param {object} api          { send(msg), length(): trimmed length in s, now(): current time in the trimmed take }
 */
export function createArrowEditor(el, api) {
  let arrows = [];
  let open = null;                   // id of the arrow whose settings are shown
  let previewUrl = null;
  let waitingPreview = false;
  let previewTimer = 0;
  let pending = new Map();           // id -> fields not yet sent
  let sendTimer = 0;

  el.innerHTML = `
    <div class="arrows-head">
      <h3>Arrows on the GIF</h3>
      <div class="arrows-add">
        <label class="sr-only" for="arrow-kind">Kind of arrow</label>
        <select id="arrow-kind">${KINDS.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select>
        <button type="button" class="btn secondary" id="arrow-add">Add arrow</button>
      </div>
    </div>
    <p class="muted arrows-help">A <b>path</b> arrow traces how a hand moved. Arrows <b>on a body part</b> move with it.
      <b>Fixed</b> shapes stay where you put them. Every arrow can be shown for only part of the GIF.</p>
    <div class="arrows-grid">
      <ol class="arrow-list" id="arrow-list"></ol>
      <figure class="arrow-preview">
        <div class="arrow-preview-img"><img id="arrow-preview" alt="Preview of the GIF frame with arrows, rendered by Blender"><span id="arrow-preview-msg">Add an arrow to see a preview.</span></div>
        <figcaption><span id="arrow-preview-cap"></span>
          <button type="button" class="link-btn" id="arrow-refresh">Refresh preview</button></figcaption>
      </figure>
    </div>`;

  const list = el.querySelector("#arrow-list");
  el.querySelector("#arrow-add").onclick = () => {
    const kind = el.querySelector("#arrow-kind").value;
    const arrow = { kind };
    if (kind === "path") arrow.grow = false;
    if (FIXED.has(kind)) arrow.part = "palm";
    if (kind === "around") arrow.part = "wrist";
    api.send({ type: "arrow_add", arrow, preview: true, t: api.now(), size: 360 });
    open = "new";
    waitingPreview = true;
    showPreviewState();
  };
  el.querySelector("#arrow-refresh").onclick = () => requestPreview(0);

  list.addEventListener("click", (e) => {
    const b = e.target.closest("[data-a]");
    if (!b) return;
    const id = +b.closest("[data-id]").dataset.id;
    if (b.dataset.a === "delete") {
      api.send({ type: "arrow_delete", id, preview: true, t: api.now(), size: 360 });
      waitingPreview = true;
      showPreviewState();
    } else if (b.dataset.a === "toggle") {
      open = open === id ? null : id;
      draw();
    } else if (b.dataset.a === "now-from" || b.dataset.a === "now-to") {
      const key = b.dataset.key;
      const t = Math.round(api.now() * 100) / 100;
      change(id, { [key]: t });
      const input = b.closest("[data-id]").querySelector(`[data-k="${key}"]`);
      if (input) input.value = t;
      syncOutputs(b.closest("[data-id]"));
    }
  });

  list.addEventListener("input", (e) => {
    const inp = e.target.closest("[data-k]");
    if (!inp) return;
    const id = +inp.closest("[data-id]").dataset.id;
    const k = inp.dataset.k;
    let v;
    if (inp.type === "checkbox") v = inp.checked;
    else if (inp.type === "range" || inp.type === "number") v = inp.value === "" ? null : +inp.value;
    else v = inp.value;
    if (k === "show_to" || k === "to") if (v !== null && v >= api.length() - 0.005) v = null;   // "until the end"
    if (k === "show_from" || k === "from") if (v !== null && v <= 0.005) v = null;
    change(id, { [k]: v });
    syncOutputs(inp.closest("[data-id]"));
    if (k === "kind" || k === "side") draw();
  });

  function change(id, fields) {
    const a = arrows.find((x) => x.id === id);
    if (a) Object.assign(a, fields);
    pending.set(id, { ...(pending.get(id) || {}), ...fields });
    clearTimeout(sendTimer);
    sendTimer = setTimeout(flush, 120);
  }

  function flush() {
    for (const [id, arrow] of pending) api.send({ type: "arrow_update", id, arrow });
    pending.clear();
    requestPreview(350);
  }

  function requestPreview(delay) {
    clearTimeout(previewTimer);
    if (!arrows.length) return;
    previewTimer = setTimeout(() => {
      waitingPreview = true;
      showPreviewState();
      api.send({ type: "preview", t: api.now(), size: 360 });
    }, delay);
  }

  function showPreviewState() {
    const msg = el.querySelector("#arrow-preview-msg");
    const img = el.querySelector("#arrow-preview");
    img.hidden = !previewUrl;
    msg.hidden = !!previewUrl && !waitingPreview;
    msg.textContent = waitingPreview ? "Blender is drawing the preview..." : arrows.length ? "" : "Add an arrow to see a preview.";
    msg.classList.toggle("over", !!previewUrl);
  }

  function fieldRow(a, [k, label, min, max, step, unit]) {
    const v = a[k] ?? 0;
    return `<div class="arrow-field"><label for="a${a.id}-${k}">${esc(label)} <output>${fmt(v, unit)}</output></label>
      <input type="range" id="a${a.id}-${k}" data-k="${k}" data-unit="${unit}" min="${min}" max="${max}" step="${step}" value="${v}"></div>`;
  }

  function timeRow(a, kFrom, kTo, label) {
    const L = Math.max(0.1, api.length());
    const f = a[kFrom] ?? 0;
    const t = a[kTo] ?? L;
    return `<fieldset class="arrow-times"><legend>${esc(label)}</legend>
      <div class="arrow-field"><label for="a${a.id}-${kFrom}">From <output>${f.toFixed(2)} s</output></label>
        <div class="time-row"><input type="range" id="a${a.id}-${kFrom}" data-k="${kFrom}" data-unit="s" min="0" max="${L.toFixed(2)}" step="0.01" value="${f}">
        <button type="button" class="link-btn" data-a="now-from" data-key="${kFrom}" title="Use the moment shown in Blender">now</button></div></div>
      <div class="arrow-field"><label for="a${a.id}-${kTo}">To <output>${t.toFixed(2)} s</output></label>
        <div class="time-row"><input type="range" id="a${a.id}-${kTo}" data-k="${kTo}" data-unit="s" min="0" max="${L.toFixed(2)}" step="0.01" value="${t}">
        <button type="button" class="link-btn" data-a="now-to" data-key="${kTo}" title="Use the moment shown in Blender">now</button></div></div>
    </fieldset>`;
  }

  function settings(a) {
    const sel = (k, opts, label) => `<div class="arrow-field"><label for="a${a.id}-${k}">${esc(label)}</label>
      <select id="a${a.id}-${k}" data-k="${k}">${opts.map(([v, l]) => `<option value="${v}" ${a[k] === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>`;
    const check = (k, label) => `<label class="check"><input type="checkbox" data-k="${k}" ${a[k] ? "checked" : ""}> ${esc(label)}</label>`;
    let html = `<div class="arrow-cols">
      ${sel("kind", KINDS, "Kind")}
      ${sel("side", [["R", "Right hand / arm"], ["L", "Left hand / arm"]], "Side")}
      ${sel("part", PARTS, a.kind === "path" ? "Follow" : FIXED.has(a.kind) ? "Place near" : "Attach to")}
    </div>`;
    if (a.kind === "path") {
      html += timeRow(a, "from", "to", "Part of the movement to trace");
      html += `<div class="arrow-cols">${PATH_FIELDS.map((f) => fieldRow(a, f)).join("")}</div>`;
      html += check("grow", "Draw the arrow as the hand moves");
    } else if (a.kind === "along" || a.kind === "around") {
      html += `<div class="arrow-cols">${sel("axis", AXES, "Axis")}${TETHER_FIELDS[a.kind].map((f) => fieldRow(a, f)).join("")}</div>`;
    } else {
      html += `<div class="arrow-cols">${FIXED_FIELDS.map((f) => fieldRow(a, f)).join("")}</div>`;
    }
    html += `<fieldset class="arrow-pos"><legend>Move the arrow</legend><div class="arrow-cols">${POSITION.map((f) => fieldRow(a, f)).join("")}</div></fieldset>`;
    html += timeRow(a, "show_from", "show_to", "Show the arrow");
    html += `<div class="arrow-cols">${STYLE_FIELDS.map((f) => fieldRow(a, f)).join("")}
      <div class="arrow-field"><label for="a${a.id}-color">Color</label>
        <input type="color" id="a${a.id}-color" data-k="color" value="${a.color || "#c8233a"}"></div></div>`;
    html += `<div class="arrow-checks">${check("reverse", "Reverse direction")}${check("double", "Arrowheads at both ends")}
      ${check("on_top", "Always in front of the body")}</div>`;
    return html;
  }

  function draw() {
    if (open === "new" && arrows.length) open = arrows[arrows.length - 1].id;
    list.innerHTML = arrows.map((a, i) => `
      <li class="arrow-item ${open === a.id ? "open" : ""}" data-id="${a.id}">
        <div class="arrow-row">
          <button type="button" class="arrow-title" data-a="toggle" aria-expanded="${open === a.id}">
            <span class="arrow-swatch" style="background:${esc(a.color || "#c8233a")}"></span>
            Arrow ${i + 1}: ${esc(kindName(a.kind))}
            <span class="muted">${esc(summary(a))}</span></button>
          <button type="button" class="link-btn danger" data-a="delete" aria-label="Delete arrow ${i + 1}">Delete</button>
        </div>
        ${open === a.id ? `<div class="arrow-settings">${settings(a)}</div>` : ""}
      </li>`).join("");
    showPreviewState();
  }

  function summary(a) {
    const part = (PARTS.find((p) => p[0] === a.part) || [0, a.part])[1].toLowerCase();
    const side = a.side === "L" ? "left" : "right";
    if (a.kind === "path") return `follows the ${side} ${part}`;
    if (FIXED.has(a.kind)) return `near the ${side} ${part}`;
    return `on the ${side} ${part}`;
  }

  function syncOutputs(item) {
    item.querySelectorAll("input[type=range]").forEach((r) => {
      const out = r.closest(".arrow-field")?.querySelector("output");
      if (out) out.textContent = fmt(+r.value, r.dataset.unit);
    });
    const sw = item.querySelector(".arrow-swatch");
    const col = item.querySelector("[data-k=color]");
    if (sw && col) sw.style.background = col.value;
  }

  return {
    /** Blender sent the take's arrows (after any change). */
    setArrows(list2) {
      const sameShape = list2.length === arrows.length && list2.every((a, i) => a.id === arrows[i].id && a.kind === arrows[i].kind);
      arrows = list2.map((a) => ({ ...a, ...(pending.get(a.id) || {}) }));
      // redraw unless the person is in the middle of changing a setting of the same arrows
      if (!sameShape || !list.contains(document.activeElement)) draw();
      if (!arrows.length) {
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = null;
        el.querySelector("#arrow-preview").removeAttribute("src");
        waitingPreview = false;
        showPreviewState();
      }
    },
    /** A preview picture arrived. */
    setPreview(buf, t) {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(new Blob([buf], { type: "image/png" }));
      el.querySelector("#arrow-preview").src = previewUrl;
      el.querySelector("#arrow-preview-cap").textContent = `At ${(+t || 0).toFixed(2)} s`;
      waitingPreview = false;
      showPreviewState();
    },
    /** The playhead moved (after seeking): refresh the preview. */
    moved() {
      if (arrows.length) requestPreview(400);
    },
    reset() {
      arrows = [];
      open = null;
      pending.clear();
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = null;
      draw();
    },
  };
}

function fmt(v, unit) {
  if (unit === "s") return `${(+v).toFixed(2)} s`;
  if (unit === "mm") return `${Math.round(v)} mm`;
  return `${Math.round(v)}${unit}`;
}
