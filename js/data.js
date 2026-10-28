// Loads the JSON files the app runs on.
// cards/manifest.json is written by the Blender flashcard editor every time
// you export a card, so new cards show up here automatically.

const cache = {};

async function load(path) {
  if (!cache[path]) {
    cache[path] = fetch(path).then((res) => {
      if (!res.ok) throw new Error(`Could not load ${path} (${res.status})`);
      return res.json();
    });
  }
  return cache[path];
}

export async function getCards() {
  try {
    const data = await load("cards/manifest.json");
    return (data.cards || []).map((c) => ({
      ...c,
      imageUrl: `cards/${c.image}`,
      quizUrl: `cards/${c.quizImage || c.image}`,
    }));
  } catch {
    return []; // no cards exported yet
  }
}

export async function getCardMap() {
  const cards = await getCards();
  return Object.fromEntries(cards.map((c) => [c.id, c]));
}

export async function getLessons() {
  return (await load("data/lessons.json")).lessons;
}

export async function getDictionary() {
  return (await load("data/dictionary.json")).terms;
}

export async function getSentences() {
  const sets = await getSentenceSets();
  return sets.filter((s) => !s.combined).flatMap((s) => s.sentences);
}

/** Practice sets for the Sentences tab: the starter set plus one per Lifeprint lesson. */
export async function getSentenceSets() {
  const starter = (await load("data/sentences.json")).sentences;
  let lifeprint = [];
  try {
    lifeprint = (await load("data/lifeprint-practice.json")).sentences;
  } catch {
    /* optional file */
  }
  const sets = [];
  const lessons = [...new Set(lifeprint.map((s) => s.lesson))].sort((a, b) => a - b);
  for (const n of lessons) {
    sets.push({ id: `lifeprint-${String(n).padStart(2, "0")}`, title: `Lifeprint lesson ${n}`,
      sentences: lifeprint.filter((s) => s.lesson === n) });
  }
  if (lifeprint.length) {
    sets.push({ id: "lifeprint-all", title: "All Lifeprint lessons", sentences: lifeprint, combined: true });
  }
  sets.unshift({ id: "starter", title: "Starter sentences", sentences: starter });
  return sets;
}

/** Every card the course needs, drawn or not (data/card-plan.json). */
export async function getCardPlan() {
  try {
    return (await load("data/card-plan.json")).cards;
  } catch {
    return [];
  }
}

// Turn a card id like "word-my" into a readable fallback label ("MY").
export function labelFromId(id) {
  return id.replace(/^[a-z]+-/, "").replace(/-/g, " ").toUpperCase();
}

export const CATEGORY_NAMES = {
  letter: "Letters",
  number: "Numbers",
  word: "Words",
  phrase: "Phrases",
  grammar: "Grammar examples",
};
