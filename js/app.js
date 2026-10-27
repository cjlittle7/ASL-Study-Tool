// Entry point: picks which page to show from the address bar (#/learn, #/practice ...).

import { renderCards } from "./cards.js";
import { initDictionary } from "./dictionary.js";
import { renderLesson, renderLessonList } from "./lessons.js";
import { renderPractice } from "./practice.js";
import { initProfile } from "./profile.js";
import { renderProgress } from "./progress.js";
import { renderSentences } from "./sentences.js";
import { esc } from "./ui.js";

const main = document.getElementById("main");

const routes = {
  learn: (arg) => (arg ? renderLesson(main, arg) : renderLessonList(main)),
  cards: () => renderCards(main),
  practice: () => renderPractice(main),
  sentences: () => renderSentences(main),
  progress: () => renderProgress(main),
};

async function route() {
  const [, page = "learn", arg] = location.hash.split("/");
  const name = routes[page] ? page : "learn";
  document.querySelectorAll(".tabs a").forEach((a) => {
    if (a.dataset.route === name) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  try {
    await routes[name](arg);
  } catch (err) {
    main.innerHTML = `<h1>Something didn't load</h1>
      <p class="error">${esc(err.message)}</p>
      <p>If you opened index.html straight from your computer, start a small local server instead
      (see the README). Browsers block loading data files from file:// pages.</p>`;
  }
  main.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", route);
document.addEventListener("profile-changed", () => {
  if (location.hash.startsWith("#/progress")) route();
});

initProfile();
initDictionary();
route();
