// "Cards" tab: browse every card exported from Blender, and see which cards
// the course still needs (data/card-plan.json).

import { CATEGORY_NAMES, getCardPlan, getCards } from "./data.js";
import { cardPicture, esc } from "./ui.js";

let filter = "all";

export async function renderCards(main) {
  const [cards, plan] = await Promise.all([getCards(), getCardPlan()]);
  const drawn = new Set(cards.map((c) => c.id));
  const todo = plan.filter((p) => !drawn.has(p.id));
  const cats = [...new Set(cards.map((c) => c.category))];
  const planned = new Set([...plan.map((p) => p.id), ...drawn]).size;

  const draw = () => {
    const shown = filter === "all" ? cards : cards.filter((c) => c.category === filter);
    main.querySelector(".card-grid").innerHTML = shown
      .map(
        (c) => `
        <button type="button" class="mini-card" data-id="${esc(c.id)}">
          ${cardPicture(c, c.id)}
          <span class="mini-meta"><span>${esc(c.term)}${c.animated ? ' <span class="badge-moving">moving</span>' : ""}</span><span>${esc(CATEGORY_NAMES[c.category] || c.category)}</span></span>
        </button>`
      )
      .join("");
    main.querySelectorAll(".chip").forEach((ch) => ch.setAttribute("aria-pressed", String(ch.dataset.cat === filter)));
  };

  main.innerHTML = `
    <h1>Cards</h1>
    <p class="lede">${cards.length} of ${planned} cards drawn. Select a card to read how it's made.</p>
    <div class="progress-bar" role="img" aria-label="${cards.length} of ${planned} cards drawn">
      <i style="width:${Math.min(100, (cards.length / planned) * 100)}%"></i></div>
    ${
      cards.length
        ? `<div class="chips" role="group" aria-label="Filter by category">
            <button type="button" class="chip" data-cat="all">All</button>
            ${cats.map((c) => `<button type="button" class="chip" data-cat="${c}">${esc(CATEGORY_NAMES[c] || c)}</button>`).join("")}
          </div>
          <div class="card-grid"></div>`
        : `<div class="empty"><p><strong>No cards yet.</strong> Open Blender, pose a sign with the ASL Flashcard Editor and click
            Export Card PNG with this project's <code>cards</code> folder selected.</p></div><div class="card-grid"></div>`
    }
    ${todo.length ? todoSection(todo) : ""}
    <dialog class="dialog" id="card-detail"></dialog>`;

  main.querySelector(".chips")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    filter = chip.dataset.cat;
    draw();
  });

  const detail = main.querySelector("#card-detail");
  main.querySelector(".card-grid").addEventListener("click", (e) => {
    const btn = e.target.closest(".mini-card");
    if (!btn) return;
    const c = cards.find((x) => x.id === btn.dataset.id);
    detail.innerHTML = `
      <h2>${esc(c.term)}</h2>
      ${cardPicture(c, c.id)}
      ${c.notes ? `<p style="margin-top:14px">${esc(c.notes)}</p>` : ""}
      <form method="dialog" class="row" style="margin-top:12px"><button class="btn">Close</button></form>`;
    detail.showModal();
  });

  const search = main.querySelector("#todo-search");
  if (search) {
    search.addEventListener("input", () => {
      const q = search.value.trim().toLowerCase();
      main.querySelectorAll(".todo-item").forEach((li) => {
        li.hidden = q !== "" && !li.dataset.term.includes(q);
      });
      main.querySelectorAll(".todo-group").forEach((g) => {
        g.hidden = !g.querySelector(".todo-item:not([hidden])");
      });
    });
  }

  draw();
}

/** The list of cards still to draw, grouped by where they're first needed. */
function todoSection(todo) {
  const groups = new Map();
  for (const p of todo) {
    const key = p.firstLesson ? `Lifeprint lesson ${p.firstLesson}` : p.sources[0] || "Other";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  }
  const order = [...groups.keys()].sort((a, b) => {
    const na = parseInt(a.replace(/\D+/g, ""), 10);
    const nb = parseInt(b.replace(/\D+/g, ""), 10);
    if (isNaN(na) !== isNaN(nb)) return isNaN(na) ? -1 : 1;
    return isNaN(na) ? a.localeCompare(b) : na - nb;
  });
  return `
    <h2>Still to draw</h2>
    <p class="muted">${todo.length} cards, listed under the first lesson that uses them. The number shows how many
      practice sentences use the sign, so higher numbers are worth drawing first.
      Card IDs match what the Blender editor exports (category + term).</p>
    <div class="field" style="max-width:360px">
      <label for="todo-search">Find a sign</label>
      <input type="search" id="todo-search" placeholder="for example: teacher">
    </div>
    ${order
      .map(
        (g) => `
      <section class="todo-group">
        <h3>${esc(g)}</h3>
        <ul class="todo-list">${groups
          .get(g)
          .sort((a, b) => b.uses - a.uses || a.term.localeCompare(b.term))
          .map(
            (p) => `<li class="todo-item" data-term="${esc(p.term.toLowerCase())}">
              <span>${esc(p.term)}</span>${p.uses ? `<span class="uses" title="Used in ${p.uses} sentences">${p.uses}</span>` : ""}</li>`
          )
          .join("")}</ul>
      </section>`
      )
      .join("")}`;
}
