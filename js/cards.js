// "Cards" tab: browse every card exported from Blender.

import { CATEGORY_NAMES, getCards } from "./data.js";
import { cardPicture, esc } from "./ui.js";

let filter = "all";

export async function renderCards(main) {
  const cards = await getCards();
  if (!cards.length) {
    main.innerHTML = `
      <h1>Cards</h1>
      <div class="empty">
        <p><strong>No cards yet.</strong> Open Blender, pose a sign with the ASL Flashcard Editor and click
        Export Card PNG with this project's <code>cards</code> folder selected. The card appears here on the next reload.</p>
      </div>`;
    return;
  }
  const cats = [...new Set(cards.map((c) => c.category))];

  const draw = () => {
    const shown = filter === "all" ? cards : cards.filter((c) => c.category === filter);
    main.querySelector(".card-grid").innerHTML = shown
      .map(
        (c) => `
        <button type="button" class="mini-card" data-id="${esc(c.id)}">
          ${cardPicture(c, c.id)}
          <span class="mini-meta"><span>${esc(c.term)}</span><span>${esc(CATEGORY_NAMES[c.category] || c.category)}</span></span>
        </button>`
      )
      .join("");
    main.querySelectorAll(".chip").forEach((ch) => ch.setAttribute("aria-pressed", String(ch.dataset.cat === filter)));
  };

  main.innerHTML = `
    <h1>Cards</h1>
    <p class="lede">${cards.length} signs so far. Select a card to read how it's made.</p>
    <div class="chips" role="group" aria-label="Filter by category">
      <button type="button" class="chip" data-cat="all">All</button>
      ${cats.map((c) => `<button type="button" class="chip" data-cat="${c}">${esc(CATEGORY_NAMES[c] || c)}</button>`).join("")}
    </div>
    <div class="card-grid"></div>
    <dialog class="dialog" id="card-detail"></dialog>`;

  main.querySelector(".chips").addEventListener("click", (e) => {
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

  draw();
}
