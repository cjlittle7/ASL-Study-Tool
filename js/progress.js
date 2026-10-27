// "Progress" tab: descriptive statistics for everything you've practiced.

import { getCardMap, getSentences, labelFromId } from "./data.js";
import { currentProfile, exportProgress, getAttempts, importProgress } from "./storage.js";
import { dailyTotals, itemStats, streak } from "./stats.js";
import { esc, pct } from "./ui.js";

/** Bars = answers per day, line = share correct that day. Hand-drawn SVG, no chart library. */
function dailyChart(rows) {
  const W = 720, H = 220, L = 36, R = 36, T = 14, B = 30;
  const iw = W - L - R, ih = H - T - B;
  const maxTotal = Math.max(5, ...rows.map((r) => r.total));
  const bw = iw / rows.length;
  const x = (i) => L + i * bw + bw / 2;
  const yCount = (n) => T + ih - (n / maxTotal) * ih;
  const yRate = (r) => T + ih - r * ih;

  const bars = rows
    .map((r, i) => r.total
      ? `<rect x="${x(i) - bw * 0.32}" y="${yCount(r.total)}" width="${bw * 0.64}" height="${T + ih - yCount(r.total)}" rx="2" fill="#d5dbe6"><title>${r.day}: ${r.total} answers</title></rect>`
      : "")
    .join("");
  const pts = rows.map((r, i) => (r.total ? [x(i), yRate(r.correct / r.total), r] : null)).filter(Boolean);
  const line = pts.length > 1 ? `<polyline fill="none" stroke="#1f8a5f" stroke-width="2.5" points="${pts.map((p) => p.slice(0, 2).join(",")).join(" ")}"/>` : "";
  const dots = pts
    .map(([px, py, r]) => `<circle cx="${px}" cy="${py}" r="4" fill="#1f8a5f"><title>${r.day}: ${pct(r.correct / r.total)} correct</title></circle>`)
    .join("");
  const labels = rows
    .map((r, i) => (i % 7 === 0 || i === rows.length - 1)
      ? `<text x="${x(i)}" y="${H - 8}" text-anchor="middle" font-size="12" fill="#5b6782">${r.date.toLocaleDateString(undefined, { month: "short", day: "numeric" })}</text>`
      : "")
    .join("");
  const grid = [0, 0.5, 1]
    .map((g) => `<line x1="${L}" x2="${W - R}" y1="${yRate(g)}" y2="${yRate(g)}" stroke="#eef1f6"/>
      <text x="${W - R + 6}" y="${yRate(g) + 4}" font-size="12" fill="#1f8a5f">${g * 100}%</text>
      <text x="${L - 6}" y="${yRate(g) + 4}" font-size="12" fill="#5b6782" text-anchor="end">${Math.round(g * maxTotal)}</text>`)
    .join("");

  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Answers per day and percent correct over the last 30 days">
    ${grid}${bars}${line}${dots}${labels}</svg>`;
}

export async function renderProgress(main) {
  const attempts = getAttempts();
  const [cardMap, sentences] = await Promise.all([getCardMap(), getSentences()]);
  const sentMap = Object.fromEntries(sentences.map((s) => [s.id, s]));
  const profile = currentProfile();

  const header = `
    <h1>${esc(profile.nickname)}'s progress</h1>
    <div class="row" style="margin:6px 0 4px">
      <button type="button" class="btn secondary" id="export">Download progress file</button>
      <label class="btn secondary" for="import" style="cursor:pointer">Load progress file</label>
      <input type="file" id="import" accept="application/json,.json" hidden>
    </div>
    <p class="muted">Use these to move your progress to another device until online sync is added.</p>
    <p id="import-msg" role="status"></p>`;

  if (!attempts.length) {
    main.innerHTML = `${header}<div class="empty"><p>No answers yet. Do a <a href="#/practice">practice round</a> and your stats will show up here.</p></div>`;
    wire(main);
    return;
  }

  const stats = [...itemStats(attempts).values()];
  const correct = attempts.filter((a) => a.correct).length;
  const last7 = attempts.filter((a) => Date.now() - new Date(a.at) < 7 * 864e5);
  const rate7 = last7.length ? pct(last7.filter((a) => a.correct).length / last7.length) : "–";
  const mastered = stats.filter((s) => s.attempts >= 3 && s.recent.slice(-3).every(Boolean)).length;
  stats.sort((a, b) => a.rate - b.rate || b.attempts - a.attempts);

  const name = (s) =>
    s.kind === "sentence"
      ? sentMap[s.item]?.english || s.item
      : cardMap[s.item]?.term || labelFromId(s.item);

  main.innerHTML = `
    ${header}
    <div class="stats">
      <div class="stat"><b>${attempts.length}</b><span>answers in total</span></div>
      <div class="stat"><b>${pct(correct / attempts.length)}</b><span>correct overall</span></div>
      <div class="stat"><b>${rate7}</b><span>correct in the last 7 days</span></div>
      <div class="stat"><b>${stats.length}</b><span>signs and sentences tried</span></div>
      <div class="stat"><b>${mastered}</b><span>right the last 3 times</span></div>
      <div class="stat"><b>${streak(attempts)}</b><span>day streak</span></div>
    </div>
    <h2>Last 30 days</h2>
    <div class="chart-wrap">${dailyChart(dailyTotals(attempts, 30))}
      <p class="muted" style="margin:6px 0 0">Gray bars: answers each day. Green line: percent correct that day.</p></div>
    <h2>Every sign and sentence</h2>
    <p class="muted">Weakest first. Dots show your last 10 answers, oldest on the left.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Item</th><th class="num">Tries</th><th class="num">Correct</th><th>Success rate</th><th>Recent</th><th>Last practiced</th></tr></thead>
      <tbody>${stats
        .map(
          (s) => `<tr>
            <td>${esc(name(s))}</td>
            <td class="num">${s.attempts}</td>
            <td class="num">${s.correct}</td>
            <td><span class="bar"><i style="width:${s.rate * 100}%"></i></span> ${pct(s.rate)}</td>
            <td><span class="dots" aria-label="${s.recent.filter(Boolean).length} of last ${s.recent.length} correct">${s.recent
              .map((ok) => `<i class="${ok ? "" : "miss"}"></i>`)
              .join("")}</span></td>
            <td>${new Date(s.last).toLocaleDateString()}</td>
          </tr>`
        )
        .join("")}</tbody>
    </table></div>`;
  wire(main);
}

function wire(main) {
  main.querySelector("#export").addEventListener("click", () => {
    const blob = new Blob([exportProgress()], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `asl-progress-${currentProfile().nickname.toLowerCase()}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
  main.querySelector("#import").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const res = importProgress(await file.text());
    const msg = main.querySelector("#import-msg");
    if (res.ok) {
      document.dispatchEvent(new CustomEvent("profile-changed"));
      renderProgress(main);
    } else {
      msg.className = "error";
      msg.textContent = res.error;
    }
  });
}
