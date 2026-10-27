// Profiles and practice history.
//
// Right now everything is saved in this browser (localStorage). Each profile
// has a nickname and a recovery code. When we add online sync (planned:
// Supabase, which works with GitHub Pages), only this file needs to change:
// the rest of the app just calls the functions exported below.

const KEY = "asl-study-tool:v1";
const GUEST = "guest";

function read() {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    /* storage blocked or corrupted: start fresh */
  }
  return { current: GUEST, profiles: { [GUEST]: newProfile("Guest", null) } };
}

let state = read();

function save() {
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    /* private window or storage full: progress lasts until the tab closes */
  }
}

function newProfile(nickname, code) {
  return { nickname, code, created: new Date().toISOString(), attempts: [] };
}

// Recovery codes avoid look-alike characters (0/O, 1/I/L).
function makeCode() {
  const chars = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  const s = Array.from(bytes, (b) => chars[b % chars.length]).join("");
  return `${s.slice(0, 4)}-${s.slice(4, 8)}-${s.slice(8, 12)}`;
}

const idFor = (nickname) => nickname.trim().toLowerCase();

export function currentProfile() {
  return state.profiles[state.current] || state.profiles[GUEST];
}

export function isGuest() {
  return state.current === GUEST;
}

/** Create a profile. Returns { ok, code } or { ok: false, error }. */
export function createProfile(nickname, keepGuestProgress = true) {
  const name = nickname.trim();
  if (name.length < 2) return { ok: false, error: "Nicknames need at least 2 characters." };
  const id = idFor(name);
  if (id === GUEST || state.profiles[id]) {
    return { ok: false, error: "That nickname is taken on this device. Try another one." };
  }
  const code = makeCode();
  const profile = newProfile(name, code);
  if (keepGuestProgress && isGuest()) {
    profile.attempts = state.profiles[GUEST].attempts;
    state.profiles[GUEST].attempts = [];
  }
  state.profiles[id] = profile;
  state.current = id;
  save();
  return { ok: true, code };
}

/** Sign in with nickname + recovery code. */
export function signIn(nickname, code) {
  const p = state.profiles[idFor(nickname)];
  const clean = code.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (!p || !p.code || p.code.replace(/-/g, "") !== clean) {
    return { ok: false, error: "That nickname and recovery code don't match a profile on this device." };
  }
  state.current = idFor(nickname);
  save();
  return { ok: true };
}

export function signOut() {
  state.current = GUEST;
  save();
}

/**
 * Save one self-assessed answer.
 * item: a card id ("letter-a") or sentence id. kind: "card" | "sentence".
 * mode: "asl-to-english" | "english-to-asl".
 */
export function recordAttempt({ item, kind, mode, correct }) {
  currentProfile().attempts.push({ item, kind, mode, correct: !!correct, at: new Date().toISOString() });
  save();
}

export function getAttempts() {
  return currentProfile().attempts;
}

/** Download-able copy of this profile, for moving to another device until sync exists. */
export function exportProgress() {
  return JSON.stringify({ app: "asl-study-tool", version: 1, profile: currentProfile() }, null, 2);
}

export function importProgress(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return { ok: false, error: "That file isn't a progress file." };
  }
  const p = data && data.profile;
  if (!p || !p.nickname || !Array.isArray(p.attempts)) {
    return { ok: false, error: "That file isn't a progress file." };
  }
  const id = idFor(p.nickname);
  const existing = state.profiles[id];
  if (existing) {
    // merge, skipping attempts we already have
    const seen = new Set(existing.attempts.map((a) => a.at + a.item));
    existing.attempts.push(...p.attempts.filter((a) => !seen.has(a.at + a.item)));
    existing.attempts.sort((a, b) => a.at.localeCompare(b.at));
  } else {
    state.profiles[id] = p;
  }
  state.current = id;
  save();
  return { ok: true, nickname: p.nickname };
}
