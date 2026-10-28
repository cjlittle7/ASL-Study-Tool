// "Sentences" tab: three ways to practice whole sentences.
//   Signs to English:  read a row of sign cards (needs every card drawn)
//   Gloss to English:  read the written gloss (works before any cards exist)
//   English to signs:  sign an English sentence, then compare
// Sentences come from data/sentences.json (starter set) and
// data/lifeprint-practice.json (Lifeprint practice sheets, lessons 1-15).

import { getCardMap, getSentenceSets, labelFromId } from "./data.js";
import { getAttempts, recordAttempt } from "./storage.js";
import { itemStats, mastery } from "./stats.js";
import { cardPicture, esc, richText, shuffle } from "./ui.js";

const settings = { mode: "gloss-to-english", set: "lifeprint-01", count: 10 };

const MODES = [
  ["asl-to-english", "Signs to English"],
  ["gloss-to-english", "Gloss to English"],
  ["english-to-asl", "English to signs"],
];

export async function renderSentences(main) {
  const [sets, cardMap] = await Promise.all([getSentenceSets(), getCardMap()]);
  if (!sets.find((s) => s.id === settings.set)) settings.set = sets[0].id;
  const fullyDrawn = (s) => s.cards.length > 0 && s.cards.every((id) => cardMap[id]);

  const pool = () => {
    const set = sets.find((s) => s.id === settings.set);
    return settings.mode === "asl-to-english" ? set.sentences.filter(fullyDrawn) : set.sentences;
  };

  const chip = (group, value, label) =>
    `<button type="button" class="chip" data-group="${group}" data-value="${value}"
      aria-pressed="${String(settings[group]) === String(value)}">${esc(label)}</button>`;

  main.innerHTML = `<div id="sent-setup">
    <h1>Sentence practice</h1>
    <p class="lede">ASL word order is different from English, so there is often more than one good answer.
      Compare yours with the answer and decide whether you got the meaning.
      New to this? Read <a href="#/learn/sentence-order">how ASL sentences are ordered</a> first.</p>
    <h2>Direction</h2>
    <div class="chips">${MODES.map(([v, l]) => chip("mode", v, l)).join("")}</div>
    <h2>Sentences</h2>
    <div class="field" style="max-width:360px">
      <label for="set">Practice set</label>
      <select id="set">${sets
        .map((s) => `<option value="${s.id}" ${s.id === settings.set ? "selected" : ""}>${esc(s.title)} (${s.sentences.length})</option>`)
        .join("")}</select>
    </div>
    <h2>How many</h2>
    <div class="chips">${[5, 10, 20].map((n) => chip("count", n, String(n))).join("")}${chip("count", 0, "All of them")}</div>
    <p class="muted" id="avail"></p>
    <button type="button" class="btn" id="start">Start</button>
    <p class="muted" style="margin-top:20px;max-width:68ch">The Lifeprint sets use practice sentences from
      Dr. Bill Vicars' <a href="https://www.lifeprint.com/" target="_blank" rel="noopener">Lifeprint.com</a>.
      English marked "added" was written for this app.</p></div>`;

  const avail = main.querySelector("#avail");
  const start = main.querySelector("#start");
  const refresh = () => {
    const n = pool().length;
    if (settings.mode === "asl-to-english") {
      avail.textContent = n
        ? `${n} sentence${n === 1 ? " has" : "s have"} every card drawn.`
        : "No sentence in this set has all of its cards drawn yet. Try Gloss to English, or draw the missing cards.";
    } else {
      avail.textContent = `${n} sentences in this set.`;
    }
    start.disabled = n === 0;
  };

  main.querySelector("#sent-setup").addEventListener("click", (e) => {
    const c = e.target.closest(".chip");
    if (!c) return;
    const g = c.dataset.group;
    settings[g] = g === "count" ? Number(c.dataset.value) : c.dataset.value;
    main.querySelectorAll(`.chip[data-group="${g}"]`).forEach((x) => x.setAttribute("aria-pressed", String(x === c)));
    refresh();
  });
  main.querySelector("#set").addEventListener("change", (e) => {
    settings.set = e.target.value;
    refresh();
  });
  start.addEventListener("click", () => {
    const stats = itemStats(getAttempts());
    let list = shuffle(pool()).sort((a, b) => mastery(stats.get(a.id)) - mastery(stats.get(b.id)));
    if (settings.count) list = list.slice(0, settings.count);
    run(main, list, cardMap);
  });
  refresh();
}

function strip(sentence, cardMap, quiz) {
  if (!sentence.cards.length) return "";
  return `<div class="strip">${sentence.cards
    .map(
      (id) => `<div class="mini-card">${cardPicture(cardMap[id], id, { quiz, label: labelFromId(id) })}
        ${quiz ? "" : `<div class="mini-meta"><span>${esc(cardMap[id]?.term || labelFromId(id))}</span></div>`}</div>`
    )
    .join("")}</div>`;
}

function run(main, list, cardMap) {
  let i = 0;
  let right = 0;
  const mode = settings.mode;

  const show = () => {
    const s = list[i];
    const prompt = {
      "asl-to-english": `<h1>What does this say in English?</h1>${strip(s, cardMap, true)}`,
      "gloss-to-english": `<h1>What does this say in English?</h1>
        <p class="lede"><span class="gloss" style="font-size:1.4rem">${esc(s.gloss)}</span></p>
        <p class="muted">This is a ${richText("[[gloss]]")}: capital words name signs, lowercase words describe how they're signed.</p>`,
      "english-to-asl": `<h1>${esc(s.english)}</h1>
        <p class="lede">Sign this sentence, or write it as a ${richText("[[gloss]]")} below.</p>`,
    }[mode];
    const label = mode === "english-to-asl" ? "Your gloss (optional)" : "Your English translation";

    main.innerHTML = `
      <p class="muted">Sentence ${i + 1} of ${list.length}${s.sheet ? ` (Lifeprint practice sheet ${esc(s.sheet)})` : ""}</p>
      ${prompt}
      <div class="field answer-box">
        <label for="ans">${label}</label>
        <textarea id="ans" rows="2"></textarea>
      </div>
      <div id="out"></div>
      <div class="row" id="actions"><button type="button" class="btn" id="check">Show answer</button></div>`;

    main.querySelector("#check").addEventListener("click", () => {
      const yours = main.querySelector("#ans").value.trim();
      main.querySelector("#out").innerHTML = `
        <div class="reveal">
          ${yours ? `<p class="muted">You wrote: ${esc(yours)}</p>` : ""}
          <p><strong>English:</strong> ${esc(s.english)}${s.englishAdded ? ` <span class="muted">(added)</span>` : ""}</p>
          <p><strong>Gloss:</strong> <span class="gloss">${esc(s.gloss)}</span></p>
          ${s.note ? `<p class="muted">${esc(s.note)}</p>` : ""}
        </div>
        ${mode === "asl-to-english" ? "" : strip(s, cardMap, false)}`;
      const actions = main.querySelector("#actions");
      actions.innerHTML = `
        <button type="button" class="btn got" id="got">I got the meaning</button>
        <button type="button" class="btn missed" id="miss">I missed it</button>`;
      const answer = (ok) => {
        recordAttempt({ item: s.id, kind: "sentence", mode, correct: ok });
        if (ok) right++;
        i++;
        i < list.length ? show() : done();
      };
      actions.querySelector("#got").addEventListener("click", () => answer(true));
      actions.querySelector("#miss").addEventListener("click", () => answer(false));
      actions.querySelector("#got").focus();
    });
    main.querySelector("#ans").focus();
  };

  const done = () => {
    main.innerHTML = `
      <h1>You got ${right} of ${list.length}</h1>
      <div class="row"><a class="btn" href="#/sentences" id="again">Go again</a>
      <a class="btn secondary" href="#/progress">See progress</a></div>`;
    main.querySelector("#again").addEventListener("click", (e) => {
      e.preventDefault();
      renderSentences(main);
    });
  };

  show();
}
