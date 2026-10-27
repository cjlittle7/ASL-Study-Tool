// "Practice" tab: self-checked flashcards.
//   Sign to English: see the sign (no label), say what it means, flip to check.
//   English to sign: see the word, make the sign yourself, flip to check.
// Cards you miss more often come up first.

import { CATEGORY_NAMES, getCards } from "./data.js";
import { getAttempts, recordAttempt } from "./storage.js";
import { itemStats, mastery } from "./stats.js";
import { cardPicture, esc, pct, shuffle } from "./ui.js";

const settings = { mode: "asl-to-english", category: "all", count: 10, order: "weak" };

export async function renderPractice(main) {
  const cards = await getCards();
  if (!cards.length) {
    main.innerHTML = `<h1>Practice</h1><div class="empty"><p>Export a few cards from the Blender editor first, then come back to practice them.</p></div>`;
    return;
  }
  const cats = [...new Set(cards.map((c) => c.category))];
  const chip = (group, value, label) =>
    `<button type="button" class="chip" data-group="${group}" data-value="${value}" aria-pressed="${String(settings[group]) === String(value)}">${esc(label)}</button>`;

  main.innerHTML = `<div id="setup">
    <h1>Practice</h1>
    <p class="lede">Answer honestly. Your results build the stats on the Progress tab and decide which cards come back sooner.</p>
    <h2>Direction</h2>
    <div class="chips">
      ${chip("mode", "asl-to-english", "Sign to English")}
      ${chip("mode", "english-to-asl", "English to sign")}
    </div>
    <h2>Cards</h2>
    <div class="chips">
      ${chip("category", "all", "All")}
      ${cats.map((c) => chip("category", c, CATEGORY_NAMES[c] || c)).join("")}
    </div>
    <h2>How many</h2>
    <div class="chips">
      ${[5, 10, 20].map((n) => chip("count", n, String(n))).join("")}
      ${chip("count", 0, "All of them")}
    </div>
    <h2>Order</h2>
    <div class="chips">
      ${chip("order", "weak", "Weakest first")}
      ${chip("order", "random", "Shuffled")}
    </div>
    <button type="button" class="btn" id="start" style="margin-top:12px">Start practice</button></div>`;

  main.querySelector("#setup").addEventListener("click", (e) => {
    const c = e.target.closest(".chip");
    if (!c) return;
    settings[c.dataset.group] = c.dataset.group === "count" ? Number(c.dataset.value) : c.dataset.value;
    main.querySelectorAll(`.chip[data-group="${c.dataset.group}"]`).forEach((x) =>
      x.setAttribute("aria-pressed", String(x === c))
    );
  });

  main.querySelector("#start").addEventListener("click", () => {
    let pool = settings.category === "all" ? cards : cards.filter((c) => c.category === settings.category);
    if (settings.order === "weak") {
      const stats = itemStats(getAttempts());
      pool = shuffle(pool).sort((a, b) => mastery(stats.get(a.id)) - mastery(stats.get(b.id)));
    } else {
      pool = shuffle(pool);
    }
    if (settings.count) pool = pool.slice(0, settings.count);
    runSession(main, pool);
  });
}

function runSession(main, deck) {
  let i = 0;
  const results = [];

  const show = () => {
    const card = deck[i];
    const signFirst = settings.mode === "asl-to-english";
    const front = signFirst
      ? `${cardPicture(card, card.id, { quiz: true })}<div class="prompt">What does this sign mean?</div>`
      : `<div class="word">${esc(card.term)}</div><div class="prompt">Make this sign, then flip to check.</div>`;
    const back = `${cardPicture(card, card.id)}${card.notes ? `<div class="hint">${esc(card.notes)}</div>` : ""}`;

    main.innerHTML = `
      <div class="stage">
        <div class="progress-line"><span>Card ${i + 1} of ${deck.length}</span>
          <button type="button" class="btn secondary" id="quit" style="min-height:34px;padding:2px 12px">End session</button></div>
        <div class="flashcard" id="fc">
          <div class="stack" aria-hidden="true"></div>
          <div class="inner">
            <div class="face front">${front}</div>
            <div class="face back" aria-hidden="true">${back}</div>
          </div>
        </div>
        <div class="row" id="actions">
          <button type="button" class="btn" id="flip">Show answer <span class="muted" style="color:#c9d3ea">(Space)</span></button>
        </div>
      </div>`;

    const fc = main.querySelector("#fc");
    const actions = main.querySelector("#actions");
    const flip = () => {
      fc.classList.add("flipped");
      fc.querySelector(".front").setAttribute("aria-hidden", "true");
      fc.querySelector(".back").removeAttribute("aria-hidden");
      actions.innerHTML = `
        <button type="button" class="btn got" id="got">I got it (G)</button>
        <button type="button" class="btn missed" id="miss">I missed it (M)</button>`;
      actions.querySelector("#got").addEventListener("click", () => answer(true));
      actions.querySelector("#miss").addEventListener("click", () => answer(false));
      actions.querySelector("#got").focus();
    };
    main.querySelector("#flip").addEventListener("click", flip);
    main.querySelector("#quit").addEventListener("click", () => finish());
    main.querySelector("#flip").focus();

    keyHandler = (e) => {
      if (e.target.matches("input, textarea")) return;
      if (e.code === "Space" && !fc.classList.contains("flipped")) {
        e.preventDefault();
        flip();
      } else if (fc.classList.contains("flipped") && e.key.toLowerCase() === "g") answer(true);
      else if (fc.classList.contains("flipped") && e.key.toLowerCase() === "m") answer(false);
    };
  };

  const answer = (correct) => {
    const card = deck[i];
    recordAttempt({ item: card.id, kind: "card", mode: settings.mode, correct });
    results.push({ card, correct });
    i++;
    if (i < deck.length) show();
    else finish();
  };

  const finish = () => {
    keyHandler = null;
    const right = results.filter((r) => r.correct).length;
    const missed = results.filter((r) => !r.correct);
    main.innerHTML = `
      <h1>${results.length ? `You got ${right} of ${results.length}` : "Session ended"}</h1>
      ${results.length ? `<p class="lede">That's ${pct(right / results.length)} this round.</p>` : ""}
      ${
        missed.length
          ? `<h2>Worth another look</h2>
             <div class="card-grid">${missed
               .map((r) => `<div class="mini-card">${cardPicture(r.card, r.card.id)}<div class="mini-meta"><span>${esc(r.card.term)}</span></div></div>`)
               .join("")}</div>`
          : ""
      }
      <div class="row" style="margin-top:24px">
        <a class="btn" href="#/practice" id="again">Practice again</a>
        <a class="btn secondary" href="#/progress">See progress</a>
      </div>`;
    main.querySelector("#again").addEventListener("click", (e) => {
      e.preventDefault();
      renderPractice(main);
    });
  };

  show();
}

// One keyboard listener for the whole app; the current card sets what it does.
let keyHandler = null;
document.addEventListener("keydown", (e) => keyHandler && keyHandler(e));
window.addEventListener("hashchange", () => (keyHandler = null));
