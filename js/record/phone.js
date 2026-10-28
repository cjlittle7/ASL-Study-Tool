// Phone page (#/phone/CODE): turns a phone into the second camera.
// The phone tracks your body, hands and face itself and sends only point positions to the laptop's
// Record tab. Set it up at any angle where it sees your head and both hands; the laptop and Blender
// work out where it is.

import { esc } from "../ui.js";
import { loadLandmarkers } from "./mediapipe.js";
import { PhoneSender } from "./phonelink.js";
import { Tracker, drawOverlay } from "./tracker.js";

let P = null;           // one phone session survives re-renders

export async function renderPhone(main, arg) {
  const code = (arg || "").split("?")[0].toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (!P) P = createPhoneSession();
  P.mount(main, code);
}

function createPhoneSession() {
  const st = {
    code: "", facing: "user", stream: null, landmarkers: null, tracker: null, loading: false,
    linkState: "off", linkError: "", fps: 0, times: [], wakeLock: null, dom: null,
  };
  const sender = new PhoneSender({
    onStatus: (s, err) => {
      st.linkState = s;
      st.linkError = err || "";
      render();
    },
  });

  async function start() {
    if (st.loading) return;
    if (!st.code) {
      setMsg("Type the code shown on the laptop first.", true);
      return;
    }
    st.loading = true;
    render();
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("This browser can't use the camera here. Open this page from its https address.");
      }
      await openCamera();
      setMsg("Loading the tracker (about 20 MB the first time)...");
      if (!st.landmarkers) st.landmarkers = await loadLandmarkers((m) => setMsg(m));
      st.tracker = new Tracker(st.dom.video, st.landmarkers, onFrame);
      st.tracker.setSmoothing(0.25);        // light: the laptop smooths again after combining
      st.tracker.start();
      setMsg("");
      sender.connect(st.code);
      keepAwake();
    } catch (err) {
      const msg = err && err.name === "NotAllowedError"
        ? "Camera permission was blocked. Allow the camera for this site in the browser settings, then try again."
        : `Couldn't start: ${err.message || err}`;
      setMsg(msg, true);
      stop();
    } finally {
      st.loading = false;
      render();
    }
  }

  async function openCamera() {
    st.stream?.getTracks().forEach((t) => t.stop());
    st.stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: st.facing, width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 30 } },
      audio: false,
    });
    const v = st.dom.video;
    v.srcObject = st.stream;
    await v.play();
  }

  function stop() {
    st.tracker?.stop();
    st.tracker = null;
    st.stream?.getTracks().forEach((t) => t.stop());
    st.stream = null;
    sender.close();
    st.wakeLock?.release?.().catch(() => {});
    st.wakeLock = null;
    if (st.dom) st.dom.video.srcObject = null;
    render();
  }

  async function keepAwake() {
    try {
      st.wakeLock = await navigator.wakeLock?.request("screen");   // keep the screen (and tracking) on
    } catch {
      /* not supported: the phone may dim; tap the screen now and then */
    }
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && st.stream && !st.wakeLock) keepAwake();
  });

  function onFrame(msg, res) {
    const now = performance.now();
    st.times.push(now);
    while (st.times.length && now - st.times[0] > 1000) st.times.shift();
    st.fps = st.times.length;
    // Only what the laptop needs: where each point is in this picture. Smaller = faster.
    const out = { t: msg.t, img: msg.img, pose2d: null, hands: [], face: null };
    if (msg.pose2d && msg.pose) out.pose2d = msg.pose2d.map((p, i) => [p[0], p[1], msg.pose[i][3]]);
    out.hands = msg.hands.map((h) => ({ side: h.side, score: h.score, img: h.img }));
    if (msg.face) out.face = { pts: msg.face.pts };
    sender.send(out);
    if (st.dom?.root.isConnected) {
      const sides = res.handRes ? st.tracker?.lastSides ?? null : null;
      drawOverlay(st.dom.overlay, st.dom.video, res, sides);
      if (now - (st._statAt || 0) > 1000) {
        st._statAt = now;
        sender.status({ ms: Math.round(st.tracker?.detectMs || 0), fps: st.fps });
        renderStatus();
      }
    }
  }

  function setMsg(text, isError = false) {
    const el = st.dom?.root.querySelector("#ph-msg");
    if (!el) return;
    el.textContent = text;
    el.hidden = !text;
    el.classList.toggle("error", isError);
  }

  function mount(main, code) {
    if (code) st.code = code;
    main.innerHTML = `
      <div class="phone">
        <h1>Phone camera</h1>
        <div class="cam-wrap phone-cam ${st.facing === "user" ? "mirrored" : ""}" id="ph-wrap">
          <video id="ph-cam" playsinline muted></video>
          <canvas id="ph-overlay" aria-hidden="true"></canvas>
        </div>
        <p class="cam-msg-inline" id="ph-msg" hidden></p>
        <div class="field phone-code" id="ph-code-field">
          <label for="ph-code">Code from the laptop</label>
          <input id="ph-code" inputmode="text" autocomplete="off" autocapitalize="characters" maxlength="6" value="${esc(st.code)}" placeholder="ABC123">
        </div>
        <div class="row phone-actions" id="ph-actions"></div>
        <dl class="status-list" id="ph-status"></dl>
        <details class="side-box phone-help" open>
          <summary>Where to put the phone</summary>
          <ul>
            <li>Stand it up to one side, about 1 to 2 meters away, so it sees your head and both hands.
              A quarter turn (45 to 90 degrees) from the laptop camera works best.</li>
            <li>Keep it still. If it moves, the laptop notices and works out its position again.</li>
            <li>After it connects, move your arms around for a few seconds so the laptop can find where the phone is.</li>
            <li>Keep this page open and the screen on.</li>
          </ul>
        </details>
      </div>`;
    const root = main.querySelector(".phone");
    st.dom = { root, video: root.querySelector("#ph-cam"), overlay: root.querySelector("#ph-overlay") };
    if (st.stream) {
      st.dom.video.srcObject = st.stream;
      st.dom.video.play().catch(() => {});
    }
    root.querySelector("#ph-code").oninput = (e) => {
      st.code = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
      e.target.value = st.code;
    };
    root.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-act]");
      if (!b || b.disabled) return;
      const a = b.dataset.act;
      if (a === "start") start();
      else if (a === "stop") stop();
      else if (a === "flip") {
        st.facing = st.facing === "user" ? "environment" : "user";
        root.querySelector("#ph-wrap").classList.toggle("mirrored", st.facing === "user");
        if (st.stream) {
          try {
            await openCamera();
          } catch (err) {
            setMsg(`Couldn't switch cameras: ${err.message || err}`, true);
          }
        }
        render();
      } else if (a === "reconnect") sender.connect(st.code);
    });
    render();
  }

  function render() {
    if (!st.dom?.root.isConnected) return;
    const root = st.dom.root;
    const on = !!st.stream;
    root.querySelector("#ph-code-field").hidden = on;
    const btn = (act, label, cls = "") => `<button type="button" class="btn ${cls}" data-act="${act}">${label}</button>`;
    let html = on ? btn("stop", "Stop", "secondary") : btn("start", st.loading ? "Starting..." : "Start phone camera");
    html += btn("flip", st.facing === "user" ? "Use back camera" : "Use front camera", "secondary");
    if (on && ["error", "closed"].includes(st.linkState)) html += btn("reconnect", "Connect again", "secondary");
    root.querySelector("#ph-actions").innerHTML = html;
    renderStatus();
  }

  function renderStatus() {
    const el = st.dom?.root.querySelector("#ph-status");
    if (!el) return;
    const link = {
      off: ["Not connected", ""], connecting: ["Connecting to the laptop...", "warn"],
      connected: ["Connected to the laptop", "ok"], closed: [st.linkError || "Disconnected", "warn"],
      error: [st.linkError || "Error", "warn"],
    }[st.linkState] || [st.linkState, ""];
    const t = st.tracker;
    const rows = [
      ["Laptop", link[0], link[1]],
      ["Camera", st.stream ? `On, ${st.fps} fps` : "Off", st.stream ? "ok" : ""],
      ["Tracking", t ? `${t.detectMs.toFixed(0)} ms per frame` : "-", t ? (t.detectMs < 45 ? "ok" : "warn") : ""],
    ];
    if (sender.dropped) rows.push(["Skipped", `${sender.dropped} late frames (Wi-Fi busy)`, ""]);
    el.innerHTML = rows.map(([k, v, c]) => `<dt>${esc(k)}</dt><dd class="${c}">${esc(v)}</dd>`).join("");
  }

  return { mount };
}
