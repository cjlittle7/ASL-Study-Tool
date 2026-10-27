// "Learn" tab: lesson list and individual lessons (content in data/lessons.json).

import { getCardMap, getLessons, labelFromId } from "./data.js";
import { cardPicture, esc, richText } from "./ui.js";

export async function renderLessonList(main) {
  const lessons = await getLessons();
  main.innerHTML = `
    <h1>Learn ASL, one building block at a time</h1>
    <p class="lede">Start with how signs are built, then the alphabet, then how sentences are put together.
      Highlighted words like <button type="button" class="term" data-term="handshape">handshape</button>
      open the dictionary.</p>
    <div class="lesson-list">
      ${lessons
        .map(
          (l, i) => `
        <a class="lesson-link" href="#/learn/${l.id}">
          <span class="lesson-num" aria-hidden="true">${i + 1}</span>
          <span><h3>${esc(l.title)}</h3><p>${esc(l.summary)}</p></span>
        </a>`
        )
        .join("")}
    </div>`;
}

function miniCard(card, id) {
  return `
    <div class="mini-card">
      ${cardPicture(card, id, { label: labelFromId(id) })}
      <div class="mini-meta"><span>${esc(card ? card.term : labelFromId(id))}</span></div>
    </div>`;
}

export async function renderLesson(main, id) {
  const [lessons, cardMap] = await Promise.all([getLessons(), getCardMap()]);
  const lesson = lessons.find((l) => l.id === id);
  if (!lesson) {
    main.innerHTML = `<h1>Lesson not found</h1><p><a href="#/learn">Back to all lessons</a></p>`;
    return;
  }
  const idx = lessons.indexOf(lesson);
  const next = lessons[idx + 1];

  const blockHtml = lesson.blocks
    .map((b) => {
      switch (b.type) {
        case "h":
          return `<h2>${esc(b.text)}</h2>`;
        case "p":
          return `<p>${richText(b.text)}</p>`;
        case "list":
          return `<ul>${b.items.map((t) => `<li>${richText(t)}</li>`).join("")}</ul>`;
        case "example":
          return `<div class="example"><p><span class="gloss">${esc(b.gloss)}</span></p><p>${esc(b.english)}</p></div>`;
        case "cards": {
          const ids = b.ids || Object.values(cardMap).filter((c) => c.category === b.category).map((c) => c.id);
          if (!ids.length) return `<p class="muted">No cards in this group yet. Export some from the Blender editor.</p>`;
          return `<div class="lesson-cards">${ids.map((cid) => miniCard(cardMap[cid], cid)).join("")}</div>`;
        }
        default:
          return "";
      }
    })
    .join("");

  main.innerHTML = `
    <article class="lesson">
      <a class="back" href="#/learn">All lessons</a>
      <h1>${esc(lesson.title)}</h1>
      <p class="lede">${esc(lesson.summary)}</p>
      ${blockHtml}
      <div class="row" style="margin-top:28px">
        ${next ? `<a class="btn" href="#/learn/${next.id}">Next: ${esc(next.title)}</a>` : ""}
        <a class="btn secondary" href="#/practice">Practice cards</a>
      </div>
    </article>`;
}
