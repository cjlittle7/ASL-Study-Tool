// Descriptive statistics over the practice history.

/** Per-item summary: attempts, correct, success rate, recent results, last time. */
export function itemStats(attempts) {
  const map = new Map();
  for (const a of attempts) {
    let s = map.get(a.item);
    if (!s) {
      s = { item: a.item, kind: a.kind, attempts: 0, correct: 0, recent: [], last: null };
      map.set(a.item, s);
    }
    s.attempts++;
    if (a.correct) s.correct++;
    s.recent.push(a.correct);
    if (s.recent.length > 10) s.recent.shift();
    s.last = a.at;
  }
  for (const s of map.values()) s.rate = s.correct / s.attempts;
  return map;
}

/**
 * How well you know an item, from 0 to 1. Uses (correct + 1) / (attempts + 2)
 * so a brand-new card counts as 50% rather than 0% or 100%.
 */
export function mastery(stat) {
  if (!stat) return 0.5;
  return (stat.correct + 1) / (stat.attempts + 2);
}

/** Results grouped by local calendar day for the last `days` days. */
export function dailyTotals(attempts, days = 30) {
  const key = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const out = [];
  const index = {};
  const today = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i);
    const row = { day: key(d), date: d, total: 0, correct: 0 };
    index[row.day] = row;
    out.push(row);
  }
  for (const a of attempts) {
    const row = index[key(new Date(a.at))];
    if (row) {
      row.total++;
      if (a.correct) row.correct++;
    }
  }
  return out;
}

/** Consecutive days (ending today or yesterday) with at least one answer. */
export function streak(attempts) {
  const days = new Set(attempts.map((a) => new Date(a.at).toDateString()));
  let count = 0;
  const d = new Date();
  if (!days.has(d.toDateString())) d.setDate(d.getDate() - 1);
  while (days.has(d.toDateString())) {
    count++;
    d.setDate(d.getDate() - 1);
  }
  return count;
}
