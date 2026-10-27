// Small shared helpers for building the page.

/** Escape text before putting it inside HTML. */
export function esc(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Turn lesson text into HTML.
 *   [[noun]]          -> clickable term "noun" that opens the dictionary
 *   [[nouns|noun]]    -> shows "nouns", opens the "noun" entry
 */
export function richText(text) {
  return esc(text).replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, label, id) => {
    const termId = (id || label).trim().toLowerCase().replace(/\s+/g, "-");
    return `<button type="button" class="term" data-term="${termId}">${label}</button>`;
  });
}

/** Picture for a card id; shows a striped placeholder if the card isn't drawn yet. */
export function cardPicture(card, id, { quiz = false, label } = {}) {
  if (card) {
    const src = quiz ? card.quizUrl : card.imageUrl;
    const alt = quiz ? "ASL sign (answer hidden)" : `ASL sign for ${card.term}`;
    return `<img src="${esc(src)}" alt="${esc(alt)}" loading="lazy">`;
  }
  return `<div class="placeholder-img"><div>${esc(label || id)}<small>Card not drawn yet</small></div></div>`;
}

export function shuffle(list) {
  const a = [...list];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

export function pct(n) {
  return `${Math.round(n * 100)}%`;
}
