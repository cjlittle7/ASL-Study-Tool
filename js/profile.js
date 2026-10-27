// The "Sign in" button and profile dialog (nickname + recovery code).

import { createProfile, currentProfile, isGuest, signIn, signOut } from "./storage.js";
import { esc } from "./ui.js";

const btn = document.getElementById("profile-btn");
const dialog = document.getElementById("profile-dialog");

export function updateProfileButton() {
  btn.textContent = isGuest() ? "Sign in" : currentProfile().nickname;
}

function changed() {
  updateProfileButton();
  document.dispatchEvent(new CustomEvent("profile-changed"));
}

function showSignedIn() {
  const p = currentProfile();
  dialog.innerHTML = `
    <h2>Hi, ${esc(p.nickname)}</h2>
    <p>Your recovery code:</p>
    <div class="code">${esc(p.code)}</div>
    <p class="notice">Keep this code somewhere safe. You'll need your nickname and this code to sign in again.</p>
    <div class="row" style="margin-top:16px">
      <button type="button" class="btn secondary" id="out">Sign out</button>
      <button type="button" class="btn" id="close">Done</button>
    </div>`;
  dialog.querySelector("#close").onclick = () => dialog.close();
  dialog.querySelector("#out").onclick = () => {
    signOut();
    changed();
    dialog.close();
  };
}

function showGuest(message = "") {
  dialog.innerHTML = `
    <h2>Save your progress</h2>
    <p>Pick a nickname and you'll get a recovery code. No email or password needed.</p>
    <form id="create">
      <div class="field"><label for="nick">Nickname</label><input type="text" id="nick" autocomplete="nickname" required></div>
      <button class="btn" type="submit">Create profile</button>
    </form>
    <h2 style="margin-top:28px">Already have one?</h2>
    <form id="login">
      <div class="field"><label for="nick2">Nickname</label><input type="text" id="nick2" required></div>
      <div class="field"><label for="code">Recovery code</label><input type="text" id="code" placeholder="ABCD-EFGH-JKLM" autocapitalize="characters" required></div>
      <div class="row"><button class="btn" type="submit">Sign in</button>
        <button class="btn secondary" type="button" id="close">Cancel</button></div>
    </form>
    <p class="error" role="alert">${esc(message)}</p>
    <p class="notice">For now profiles are saved in this browser. Online sync between devices is coming next;
      until then, use Download progress file on the Progress tab.</p>`;

  dialog.querySelector("#close").onclick = () => dialog.close();
  dialog.querySelector("#create").onsubmit = (e) => {
    e.preventDefault();
    const res = createProfile(dialog.querySelector("#nick").value);
    if (!res.ok) return showGuest(res.error);
    changed();
    showSignedIn();
  };
  dialog.querySelector("#login").onsubmit = (e) => {
    e.preventDefault();
    const res = signIn(dialog.querySelector("#nick2").value, dialog.querySelector("#code").value);
    if (!res.ok) return showGuest(res.error);
    changed();
    dialog.close();
  };
}

export function initProfile() {
  updateProfileButton();
  btn.addEventListener("click", () => {
    isGuest() ? showGuest() : showSignedIn();
    dialog.showModal();
  });
}
