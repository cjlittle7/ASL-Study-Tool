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
  return (await load("data/sentences.json")).sentences;
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
