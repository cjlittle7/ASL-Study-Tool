// "Sentences" tab: translate a row of sign cards into English, or an English
// sentence into signs. Sentences live in data/sentences.json and list card ids
// in ASL order. Cards not drawn yet show a striped placeholder with the gloss.

import { getCardMap, getSentences, labelFromId } from "./data.js";
import { getAttempts, recordAttempt } from "./storage.js";
import { itemStats, mastery } from "./stats.js";
import { cardPicture, esc, richText, shuffle } from "./ui.js";

let mode = "asl-to-english";

export async function renderSentences(main) {
  const [sentences, cardMap] = await Promise.all([getSentences(), getCardMap()]);

  main.innerHTML = `<div id="sent-setup">
    <h1>Sentence practice</h1>
    <p class="lede">ASL word order is different from English, so there is often more than one good answer.
      Compare yours with the answer and decide for yourself whether you got the meaning.
      See <a href="#/learn/sentence-order">how ASL sentences are ordered</a> first if this is new.</p>
    <div class="chips">
      <button type="button" class="chip" data-mode="asl-to-english" aria-pressed="${mode === "asl-to-english"}">Signs to English</button>
      <button type="button" class="chip" data-mode="english-to-asl" aria-pressed="${mode === "english-to-asl"}">English to signs</button>
    </div>
    <button type="button" class="btn" id="start">Start</button>
    <p class="muted" style="margin-top:16px">${sentences.length} sentences available.</p></div>`;

  main.querySelector("#sent-setup").addEventListener("click", (e) => {
    const c = e.target.closest(".chip");
    if (!c) return;
    mode = c.dataset.mode;
    main.querySelectorAll(".chip").forEach((x) => x.setAttribute("aria-pressed", String(x === c)));
  });
  main.querySelector("#start").addEventListener("click", () => {
    // Reading signs needs every card drawn; otherwise the placeholder would show the answer.
    const usable = mode === "asl-to-english" ? sentences.filter((s) => s.cards.every((id) => cardMap[id])) : sentences;
    if (!usable.length) {
      main.querySelector("#start").insertAdjacentHTML("afterend",
        `<p class="error">No sentence has all of its cards drawn yet. Try English to signs, or export the missing cards.</p>`);
      return;
    }
    const stats = itemStats(getAttempts());
    const order = shuffle(usable).sort((a, b) => mastery(stats.get(a.id)) - mastery(stats.get(b.id)));
    run(main, order, cardMap);
  });
}

function strip(sentence, cardMap, quiz) {
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

  const show = () => {
    const s = list[i];
    const signsFirst = mode === "asl-to-english";
    main.innerHTML = `
      <p class="muted">Sentence ${i + 1} of ${list.length}</p>
      <h1>${signsFirst ? "What does this say in English?" : esc(s.english)}</h1>
      ${signsFirst ? strip(s, cardMap, true) : `<p class="lede">Sign this sentence, or write it as a ${richText("[[gloss]]")} below.</p>`}
      <div class="field answer-box">
        <label for="ans">${signsFirst ? "Your English translation" : "Your gloss (optional)"}</label>
        <textarea id="ans" rows="2"></textarea>
      </div>
      <div id="out"></div>
      <div class="row" id="actions"><button type="button" class="btn" id="check">Show answer</button></div>`;

    main.querySelector("#check").addEventListener("click", () => {
      const yours = main.querySelector("#ans").value.trim();
      main.querySelector("#out").innerHTML = `
        <div class="reveal">
          ${yours ? `<p class="muted">You wrote: ${esc(yours)}</p>` : ""}
          <p><strong>English:</strong> ${esc(s.english)}</p>
          <p><strong>Gloss:</strong> <span class="gloss">${esc(s.gloss)}</span></p>
          ${s.note ? `<p class="muted">${esc(s.note)}</p>` : ""}
        </div>
        ${signsFirst ? "" : strip(s, cardMap, false)}`;
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
