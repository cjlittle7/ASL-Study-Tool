// "Record" tab: webcam tracking that moves the figure in Blender, plus recording signs
// as animated GIF cards. Blender holds the recording; this page is the camera and the remote.

import { esc } from "../ui.js";
import { BlenderLink } from "./link.js";
import { loadLandmarkers, MP_VERSION } from "./mediapipe.js";
import { Tracker, assignSides, drawOverlay } from "./tracker.js";
import { createArrowEditor } from "./arrows.js";

const SETTINGS_KEY = "asl-study-tool:record-settings";
const defaults = { port: 8766, mirrorPreview: true, mirrorFigure: false, autoStop: true, countdown: 3,
                   smoothing: 0.5, face: "auto", category: "phrase", quality: "FAST", fps: 15, size: 480,
                   useDepth: true, fov: 60 };

function loadSettings() {
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") };
  } catch {
    return { ...defaults };
  }
}
function saveSettings(s) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
  } catch {
    /* private window: settings last until reload */
  }
}

// One session survives page switches, so the camera and link keep running in the background.
let S = null;

export async function renderRecord(main) {
  if (!S) S = createSession();
  S.mount(main);
}

function createSession() {
  const st = {
    settings: loadSettings(),
    stream: null, landmarkers: null, tracker: null, loading: false,
    blender: null,            // last state from Blender
    hello: null,
    linkStatus: "idle",
    snaps: [],                // { t (ms, capture clock), url }
    capturing: false,
    lastSides: null,
    gifUrl: null, gifName: "", gifInfo: null, expectBin: null,
    arrowEd: null, takeId: null, viewT: 0,
    playhead: null, fps: 0, frameTimes: [],
    dom: null,
  };

  const link = new BlenderLink({
    onStatus: (s) => {
      st.linkStatus = s;
      if (s === "open") pushSettings();
      if (s !== "open") st.blender = null;
      render();
    },
    onMessage: (m) => onBlender(m),
    onBinary: (buf) => onBinary(buf),
  });

  function pushSettings() {
    const s = st.settings;
    link.send({ type: "settings", mirror: s.mirrorFigure, auto_stop: s.autoStop, countdown: s.countdown,
                use_depth: s.useDepth, fov: s.fov });
  }

  // --- messages from Blender ---------------------------------------------------------
  function onBlender(m) {
    if (m.type === "hello") st.hello = m;
    else if (m.type === "state") {
      const prevMode = st.blender?.mode;
      st.blender = m;
      if (m.mode === "countdown" || m.mode === "recording") {
        if (!st.capturing) startSnaps();
      } else if (st.capturing) {
        st.capturing = false;
      }
      if (m.mode === "live" && prevMode && prevMode !== "live" && prevMode !== "rendering") clearSnaps();
      if (m.mode !== "playing") st.playhead = null;
      render();
    } else if (m.type === "playhead") {
      st.playhead = m.t;
      st.viewT = m.t;
      updateTimeline();
    } else if (m.type === "render_progress") {
      if (st.blender) st.blender.progress = { stage: m.stage, done: m.done, total: m.total };
      updateProgress();
    } else if (m.type === "gif_ready") {
      st.expectBin = { kind: "gif", info: m };
    } else if (m.type === "preview_ready") {
      st.expectBin = { kind: "preview", info: m };
    } else if (m.type === "arrows") {
      st.arrowEd?.setArrows(m.arrows || []);
    } else if (m.type === "notice") {
      flash(m.message);
    }
  }

  function onBinary(buf) {
    const exp = st.expectBin;
    st.expectBin = null;
    if (!exp) return;
    if (exp.kind === "preview") {
      st.arrowEd?.setPreview(buf, exp.info.t);
      return;
    }
    if (st.gifUrl) URL.revokeObjectURL(st.gifUrl);
    st.gifUrl = URL.createObjectURL(new Blob([buf], { type: "image/gif" }));
    st.gifInfo = exp.info;
    st.gifName = exp.info.name || "sign.gif";
    render();
    st.dom?.root.querySelector("#result")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // --- camera + tracking -----------------------------------------------------------------
  async function startCamera() {
    if (st.loading) return;
    st.loading = true;
    setCamMsg("Starting the camera...");
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("This browser can't use the camera here. Open the app from http://localhost or an https address.");
      }
      st.stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 60 }, facingMode: "user" },
        audio: false,
      });
      const v = st.dom.video;
      v.srcObject = st.stream;
      await v.play();
      if (!st.landmarkers) st.landmarkers = await loadLandmarkers(setCamMsg);
      st.tracker = new Tracker(v, st.landmarkers, onFrame);
      applyTrackerSettings();
      st.tracker.start();
      setCamMsg("");
    } catch (err) {
      const msg = err && err.name === "NotAllowedError"
        ? "Camera permission was blocked. Allow the camera in the address bar, then try again."
        : err && err.name === "NotFoundError" ? "No camera was found." : `Couldn't start tracking: ${err.message || err}`;
      setCamMsg(msg, true);
      stopCamera(false);
    } finally {
      st.loading = false;
      render();
    }
  }

  function stopCamera(rerender = true) {
    st.tracker?.stop();
    st.tracker = null;
    st.stream?.getTracks().forEach((t) => t.stop());
    st.stream = null;
    if (st.dom) st.dom.video.srcObject = null;
    if (rerender) render();
  }

  function applyTrackerSettings() {
    const t = st.tracker;
    if (!t) return;
    t.setSmoothing(st.settings.smoothing);
    const f = st.settings.face;
    t.autoFace = f === "auto";
    t.faceEvery = f === "off" ? 0 : f === "2" ? 2 : 1;
  }

  function onFrame(msg, res) {
    const now = performance.now();
    st.frameTimes.push(now);
    while (st.frameTimes.length && now - st.frameTimes[0] > 1000) st.frameTimes.shift();
    st.fps = st.frameTimes.length;
    link.sendFrame(msg);
    if (st.capturing) snap(msg.t);
    if (st.dom && st.dom.root.isConnected) {
      st.lastSides = res.handRes ? assignSides(res.handRes, res.poseRes?.landmarks?.[0]) : null;
      drawOverlay(st.dom.overlay, st.dom.video, res, st.lastSides);
      if (now - (st._statsAt || 0) > 250) {
        st._statsAt = now;
        updateStats(msg);
      }
    }
  }

  // --- small camera pictures for reviewing a take -----------------------------------------
  function startSnaps() {
    clearSnaps();
    st.capturing = true;
  }
  function clearSnaps() {
    for (const s of st.snaps) URL.revokeObjectURL(s.url);
    st.snaps = [];
  }
  let snapCanvas = null;
  let lastSnap = 0;
  function snap(t) {
    if (t - lastSnap < 60) return;           // ~16 pictures a second is plenty for reviewing
    lastSnap = t;
    const v = st.dom?.video;
    if (!v || !v.videoWidth) return;
    if (!snapCanvas) snapCanvas = document.createElement("canvas");
    snapCanvas.width = 192;
    snapCanvas.height = Math.round((192 * v.videoHeight) / v.videoWidth);
    snapCanvas.getContext("2d").drawImage(v, 0, 0, snapCanvas.width, snapCanvas.height);
    snapCanvas.toBlob((b) => b && st.snaps.push({ t, url: URL.createObjectURL(b) }), "image/jpeg", 0.7);
  }
  function snapAt(tSec) {
    const take = st.blender?.take;
    if (!take || !st.snaps.length) return null;
    const target = take.t0_ms + tSec * 1000;
    let best = st.snaps[0];
    for (const s of st.snaps) if (Math.abs(s.t - target) < Math.abs(best.t - target)) best = s;
    return best.url;
  }

  // --- actions ---------------------------------------------------------------------------
  const act = {
    record: () => link.send({ type: "record", action: "start", countdown: st.settings.countdown }),
    stop: () => link.send({ type: "record", action: "stop" }),
    retry: () => {
      clearGif();
      link.send({ type: "record", action: "retry", countdown: st.settings.countdown });
    },
    discard: () => {
      clearGif();
      link.send({ type: "record", action: "discard" });
    },
    play: () => link.send({ type: st.blender?.mode === "playing" ? "pause" : "play" }),
    calibrate: () => link.send({ type: "calibrate" }),
    snapshot: () => link.send({ type: "snapshot" }),
    gif: () => {
      const s = st.settings;
      const text = st.dom.root.querySelector("#desc")?.value.trim() || "";
      clearGif();
      link.send({ type: "render_gif", text, category: s.category, fps: s.fps, size: s.size, quality: s.quality });
    },
  };
  function clearGif() {
    if (st.gifUrl) URL.revokeObjectURL(st.gifUrl);
    st.gifUrl = null;
    st.gifInfo = null;
  }

  // --- rendering the page ------------------------------------------------------------------
  function mount(main) {
    main.innerHTML = template();
    const root = main.querySelector(".rec");
    st.dom = { root, video: root.querySelector("#cam"), overlay: root.querySelector("#overlay") };
    if (st.stream) {
      st.dom.video.srcObject = st.stream;
      st.dom.video.play().catch(() => {});
    }
    wire(root);
    render();
  }

  function template() {
    const s = st.settings;
    const opt = (v, cur, label) => `<option value="${v}" ${String(v) === String(cur) ? "selected" : ""}>${esc(label)}</option>`;
    return `
    <div class="rec">
      <h1>Record a sign</h1>
      <p class="lede">Your webcam moves the figure in Blender as you sign. Record a sign, trim it, describe it,
        and save it as an animated card.</p>
      <div class="rec-layout">
        <section class="rec-stage">
          <div class="cam-wrap ${s.mirrorPreview ? "mirrored" : ""}" id="cam-wrap">
            <video id="cam" playsinline muted></video>
            <canvas id="overlay" aria-hidden="true"></canvas>
            <div class="cam-msg" id="cam-msg"></div>
            <div class="countdown" id="countdown" hidden></div>
            <div class="rec-badge" id="rec-badge" hidden></div>
          </div>
          <div class="rec-actions" id="actions"></div>
          <div id="notice" class="notice" role="status" hidden></div>
          <section id="review" class="review" hidden></section>
          <section id="result" class="result" hidden></section>
        </section>
        <aside class="rec-side">
          <section class="side-box">
            <h2>Status</h2>
            <dl class="status-list" id="status"></dl>
          </section>
          <section class="side-box">
            <h2>Settings</h2>
            <label class="check"><input type="checkbox" id="s-mirror-preview" ${s.mirrorPreview ? "checked" : ""}> Show the camera like a mirror</label>
            <label class="check"><input type="checkbox" id="s-mirror-figure" ${s.mirrorFigure ? "checked" : ""}> Figure mirrors me (left-handed signers)</label>
            <label class="check"><input type="checkbox" id="s-autostop" ${s.autoStop ? "checked" : ""}> Stop recording when my hands drop</label>
            <label class="check"><input type="checkbox" id="s-depth" ${s.useDepth ? "checked" : ""}> Place hands by position, so touching my face touches the figure's face</label>
            <div class="field"><label for="s-fov">Camera field of view</label>
              <select id="s-fov">${[50, 55, 60, 65, 70, 78, 90].map((n) => opt(n, s.fov, n + "°" + (n === 60 ? " (most webcams)" : n === 78 ? " (wide webcams)" : ""))).join("")}</select>
              <span class="muted small">If your hands land in front of the figure's face, choose a wider angle; behind it, a narrower one.</span></div>
            <div class="field"><label for="s-countdown">Countdown before recording</label>
              <select id="s-countdown">${[0, 2, 3, 5].map((n) => opt(n, s.countdown, n ? `${n} seconds` : "None")).join("")}</select></div>
            <div class="field"><label for="s-smooth">Smoothing <span class="muted" id="s-smooth-val"></span></label>
              <input type="range" id="s-smooth" min="0" max="1" step="0.05" value="${s.smoothing}"></div>
            <div class="field"><label for="s-face">Face tracking</label>
              <select id="s-face">${opt("auto", s.face, "Automatic (fastest)")}${opt("1", s.face, "Every frame")}${opt("2", s.face, "Every other frame")}${opt("off", s.face, "Off")}</select></div>
            <div class="field"><label for="s-port">Blender port</label>
              <input type="text" inputmode="numeric" id="s-port" value="${s.port}" style="max-width:120px"></div>
          </section>
          <details class="side-box">
            <summary>How to connect Blender</summary>
            <ol class="steps">
              <li>Install the ASL Flashcard Editor add-on (version 0.6 or newer).</li>
              <li>In Blender press <kbd>N</kbd>, open <b>ASL Cards</b>, click <b>New Card Scene</b> if needed.</li>
              <li>In <b>Live Tracking &amp; Recording</b>, click <b>Start Live Link</b>.</li>
              <li>Click <b>Start camera</b> on this page. The status turns green when they find each other.</li>
            </ol>
            <p class="muted">For the quickest response, use Blender's Solid viewport shading while tracking.
              Tracking runs in this browser with MediaPipe ${MP_VERSION}; no video leaves your computer.</p>
          </details>
        </aside>
      </div>
    </div>`;
  }

  function wire(root) {
    const s = st.settings;
    const save = () => {
      saveSettings(s);
      applyTrackerSettings();
    };
    root.querySelector("#s-mirror-preview").onchange = (e) => {
      s.mirrorPreview = e.target.checked;
      root.querySelector("#cam-wrap").classList.toggle("mirrored", s.mirrorPreview);
      save();
    };
    root.querySelector("#s-mirror-figure").onchange = (e) => {
      s.mirrorFigure = e.target.checked;
      save();
      pushSettings();
    };
    root.querySelector("#s-autostop").onchange = (e) => {
      s.autoStop = e.target.checked;
      save();
      pushSettings();
    };
    root.querySelector("#s-depth").onchange = (e) => {
      s.useDepth = e.target.checked;
      save();
      pushSettings();
    };
    root.querySelector("#s-fov").onchange = (e) => {
      s.fov = +e.target.value;
      save();
      pushSettings();
    };
    root.querySelector("#s-countdown").onchange = (e) => {
      s.countdown = +e.target.value;
      save();
      pushSettings();
    };
    const smooth = root.querySelector("#s-smooth");
    const smoothLabel = () => (root.querySelector("#s-smooth-val").textContent =
      s.smoothing <= 0.001 ? "(off)" : s.smoothing < 0.35 ? "(light)" : s.smoothing < 0.7 ? "(medium)" : "(strong)");
    smooth.oninput = () => {
      s.smoothing = +smooth.value;
      smoothLabel();
      save();
    };
    smoothLabel();
    root.querySelector("#s-face").onchange = (e) => {
      s.face = e.target.value;
      save();
    };
    root.querySelector("#s-port").onchange = (e) => {
      const p = parseInt(e.target.value, 10);
      if (p >= 1024 && p <= 65535) {
        s.port = p;
        save();
        link.disconnect();
        link.connect(p);
      } else e.target.value = s.port;
    };

    root.addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]");
      if (!b || b.disabled) return;
      const a = b.dataset.act;
      if (a === "camera") st.stream ? stopCamera() : startCamera();
      else if (a === "connect") link.want ? link.disconnect() : link.connect(st.settings.port);
      else if (act[a]) act[a]();
    });

    keyHandler = (e) => {
      if (!st.dom?.root.isConnected || e.target.matches("input, textarea, select")) return;
      const mode = st.blender?.mode;
      if (e.key === "r" || e.key === "R") {
        if (mode === "live") act.record();
        else if (mode === "review") act.retry();
      } else if (e.code === "Space") {
        if (mode === "recording" || mode === "countdown") {
          e.preventDefault();
          act.stop();
        } else if (mode === "review" || mode === "playing") {
          e.preventDefault();
          act.play();
        }
      } else if (e.key === "Escape" && mode === "countdown") act.stop();
    };
  }

  function setCamMsg(text, isError = false) {
    const el = st.dom?.root.querySelector("#cam-msg");
    if (!el) return;
    el.textContent = text;
    el.hidden = !text;
    el.classList.toggle("error", isError);
  }

  let flashTimer = null;
  function flash(text) {
    const el = st.dom?.root.querySelector("#notice");
    if (!el) return;
    el.textContent = text;
    el.hidden = false;
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => (el.hidden = true), 5000);
  }

  function updateStats(msg) {
    const el = st.dom?.root.querySelector("#status");
    if (!el) return;
    const L = link.latency;
    const t = st.tracker;
    const handsN = msg ? msg.hands.length : 0;
    const linkText = {
      idle: "Not connected", connecting: "Looking for Blender...", open: "Connected",
      closed: "Connection lost. Retrying...", unreachable: "Not found. Is Start Live Link on?", error: "Error",
    }[st.linkStatus] || st.linkStatus;
    const rows = [
      ["Camera", st.stream ? `On, ${st.fps} fps` : "Off", st.stream ? "ok" : ""],
      ["Tracking", t ? `${t.detectMs.toFixed(0)} ms per frame${st.landmarkers?.delegate === "CPU" ? " (CPU)" : ""}` : "Off",
        t ? (t.detectMs < 30 ? "ok" : "warn") : ""],
      ["Seen", msg ? `${handsN} hand${handsN === 1 ? "" : "s"}${msg.pose ? ", body" : ""}${msg.face ? ", face" : ""}` : "-", ""],
      ["Blender", linkText + (st.hello ? ` (add-on ${st.hello.addon})` : ""), link.connected ? "ok" : st.linkStatus === "unreachable" ? "warn" : ""],
    ];
    if (link.connected && L.total) {
      rows.push(["Delay", `${L.total.toFixed(0)} ms camera to model`, L.total < 120 ? "ok" : "warn"]);
      rows.push(["", `tracking ${t ? t.detectMs.toFixed(0) : "-"} ms, Blender ${L.blender.toFixed(1)} ms`, ""]);
    }
    const touch = link.connected && msg && msg.hands.length ? link.touch : null;
    if (touch) {
      const parts = Object.entries(touch).filter(([, v]) => v > 0.5).map(([k]) => (k === "R" ? "right" : "left"));
      rows.push(["Touching", parts.length ? `${parts.join(" and ")} hand on face or chest` : "-", parts.length ? "ok" : ""]);
    }
    if (st.blender?.calibrating) rows.push(["", "Calibrating: face the camera and hold still", "warn"]);
    el.innerHTML = rows.map(([k, v, c]) => `<dt>${esc(k)}</dt><dd class="${c}">${esc(v)}</dd>`).join("");
  }

  function render() {
    if (!st.dom || !st.dom.root.isConnected) return;
    const root = st.dom.root;
    const b = st.blender;
    const mode = b?.mode || (link.connected ? "live" : "off");
    const connected = link.connected;
    const camOn = !!st.stream;

    if (!camOn && !st.loading) setCamMsg(root.querySelector("#cam-msg").classList.contains("error")
      ? root.querySelector("#cam-msg").textContent : "Click Start camera to begin.", root.querySelector("#cam-msg").classList.contains("error"));

    // countdown + recording badges
    const cd = root.querySelector("#countdown");
    cd.hidden = mode !== "countdown";
    if (mode === "countdown") cd.textContent = Math.max(1, Math.ceil(b.countdown || 0));
    const badge = root.querySelector("#rec-badge");
    badge.hidden = mode !== "recording";
    if (mode === "recording") badge.textContent = "Recording";

    // main buttons
    const btn = (actName, label, cls = "", disabled = false, title = "") =>
      `<button type="button" class="btn ${cls}" data-act="${actName}" ${disabled ? "disabled" : ""} ${title ? `title="${esc(title)}"` : ""}>${label}</button>`;
    let html = btn("camera", camOn ? "Stop camera" : st.loading ? "Starting..." : "Start camera", camOn ? "secondary" : "", st.loading);
    if (!connected) {
      html += btn("connect", link.want ? "Stop looking for Blender" : "Connect to Blender", "secondary");
    }
    if (mode === "live" || mode === "off") {
      const why = !connected ? "Connect to Blender first" : !camOn ? "Start the camera first" : "";
      html += btn("record", "Record sign <kbd>R</kbd>", "rec-btn", !!why, why);
      if (connected) {
        html += btn("calibrate", "Calibrate", "secondary", !camOn, "Face the camera with a relaxed face and hold still");
        html += btn("snapshot", "Copy pose to Blender sliders", "secondary", !camOn);
      }
    } else if (mode === "countdown") {
      html += btn("stop", "Cancel <kbd>Esc</kbd>", "secondary");
    } else if (mode === "recording") {
      html += btn("stop", "Stop <kbd>Space</kbd>", "stop-btn");
    }
    root.querySelector("#actions").innerHTML = html;

    // review
    const review = root.querySelector("#review");
    const showReview = connected && b?.take && ["review", "playing", "rendering"].includes(mode);
    review.hidden = !showReview;
    if (showReview) renderReview(review, b);

    // result
    const result = root.querySelector("#result");
    result.hidden = !st.gifUrl;
    if (st.gifUrl) {
      const saved = st.gifInfo?.saved;
      const where = !saved ? "Not saved to the app: set the Cards folder in Blender's Card panel."
        : saved.in_app ? `${saved.replaced ? "Replaced" : "Added"} card <code>${esc(saved.card_id)}</code> in the app. Reload the Cards tab to see it.`
        : "Saved in the cards/motion folder. Add a description to also add it to the app.";
      result.innerHTML = `
        <h2>Your GIF</h2>
        <img src="${st.gifUrl}" alt="Animated GIF of the recorded sign" class="gif-out">
        <p class="muted">${st.gifInfo?.frames || ""} frames, ${(st.gifInfo?.bytes / 1024 || 0).toFixed(0)} KB. ${where}</p>
        <div class="row">
          <a class="btn" href="${st.gifUrl}" download="${esc(st.gifName)}">Download GIF</a>
          ${btn("retry", "Record again <kbd>R</kbd>", "secondary")}
        </div>`;
    }
    updateStats(null);
  }

  function renderReview(el, b) {
    const take = b.take;
    const s = st.settings;
    const rendering = b.mode === "rendering";
    if (!el.querySelector("#tl")) {
      const opt = (v, cur, label) => `<option value="${v}" ${String(v) === String(cur) ? "selected" : ""}>${esc(label)}</option>`;
      el.innerHTML = `
        <h2>Review the take</h2>
        <p class="muted">Drag the handles to cut off the start and end. The figure in Blender shows the moment you're on.</p>
        <div class="review-grid">
          <figure class="snap ${s.mirrorPreview ? "mirrored" : ""}"><img id="snap" alt="Your camera at this moment"><figcaption id="snap-cap"></figcaption></figure>
          <div class="review-main">
            <div class="timeline" id="tl">
              <div class="film" id="film"></div>
              <div class="sel" id="sel"></div>
              <div class="playhead" id="ph" hidden></div>
              <input type="range" id="t-start" aria-label="Start of the sign" min="0" step="0.01">
              <input type="range" id="t-end" aria-label="End of the sign" min="0" step="0.01">
            </div>
            <div class="times" id="times"></div>
            <div class="row" style="margin:10px 0 18px">
              <button type="button" class="btn secondary" data-act="play" id="play-btn">Play <kbd>Space</kbd></button>
              <button type="button" class="btn secondary" data-act="retry">Retry <kbd>R</kbd></button>
              <button type="button" class="btn secondary" data-act="discard">Discard</button>
            </div>
            <div class="field"><label for="desc">What does this sign mean?</label>
              <input type="text" id="desc" maxlength="120" placeholder="for example: Nice to meet you" autocomplete="off"></div>
            <section class="arrows" id="arrows"></section>
            <div class="gif-opts">
              <div class="field"><label for="g-cat">Category</label><select id="g-cat">${opt("phrase", s.category, "Phrase")}${opt("word", s.category, "Word")}</select></div>
              <div class="field"><label for="g-look">Look</label><select id="g-look">${opt("FAST", s.quality, "Fast")}${opt("CARD", s.quality, "Card look (slower)")}</select></div>
              <div class="field"><label for="g-fps">Frame rate</label><select id="g-fps">${[10, 15, 20, 30].map((n) => opt(n, s.fps, n + " fps")).join("")}</select></div>
              <div class="field"><label for="g-size">Size</label><select id="g-size">${[360, 480, 540, 720].map((n) => opt(n, s.size, n + " px")).join("")}</select></div>
            </div>
            <button type="button" class="btn" data-act="gif" id="gif-btn">Make GIF</button>
            <div class="progress-bar gif-progress" id="gif-prog" hidden><i></i></div>
            <p class="muted" id="gif-prog-text"></p>
          </div>
        </div>`;
      const sel = (id, key, num) => {
        el.querySelector(id).onchange = (e) => {
          s[key] = num ? +e.target.value : e.target.value;
          saveSettings(s);
        };
      };
      sel("#g-cat", "category");
      sel("#g-look", "quality");
      sel("#g-fps", "fps", true);
      sel("#g-size", "size", true);
      const a = el.querySelector("#t-start"), z = el.querySelector("#t-end");
      let raf = 0, pending = null;
      const seek = (t) => {
        pending = t;
        st.viewT = t;
        if (!raf) raf = requestAnimationFrame(() => {
          raf = 0;
          link.send({ type: "seek", t: pending });
        });
      };
      a.oninput = () => {
        if (+a.value > +z.value - 0.1) a.value = Math.max(0, +z.value - 0.1);
        localTrim(+a.value, +z.value, +a.value);
        seek(+a.value);
      };
      z.oninput = () => {
        if (+z.value < +a.value + 0.1) z.value = Math.min(+z.max, +a.value + 0.1);
        localTrim(+a.value, +z.value, +z.value);
        seek(+z.value);
      };
      const commit = () => {
        link.send({ type: "trim", start: +a.value, end: +z.value });
        st.arrowEd?.moved();
      };
      a.onchange = commit;
      z.onchange = commit;
      el.querySelector("#desc").value = st.lastDesc || "";
      el.querySelector("#desc").oninput = (e) => (st.lastDesc = e.target.value);
      st.arrowEd = createArrowEditor(el.querySelector("#arrows"), {
        send: (m) => link.send(m),
        length: () => {
          const tk = st.blender?.take;
          return tk ? tk.end - tk.start : 0;
        },
        now: () => {
          const tk = st.blender?.take;
          return tk ? Math.min(tk.end - tk.start, Math.max(0, st.viewT - tk.start)) : 0;
        },
      });
      st.takeId = null;
    }
    if (st.takeId !== take.t0_ms) {           // a new take: show its arrows (none yet)
      st.takeId = take.t0_ms;
      st.viewT = take.start;
      st.arrowEd.reset();
      st.arrowEd.setArrows(take.arrows || []);
    }
    const a = el.querySelector("#t-start"), z = el.querySelector("#t-end");
    if (+a.max !== take.duration || el.dataset.samples !== String(take.samples)) {
      a.max = z.max = take.duration;
      el.dataset.samples = String(take.samples);
      buildFilm(el.querySelector("#film"), take.duration);
    }
    if (document.activeElement !== a) a.value = take.start;
    if (document.activeElement !== z) z.value = take.end;
    localTrim(take.start, take.end, st.playhead ?? take.start);
    el.querySelector("#play-btn").innerHTML = b.mode === "playing" ? "Pause <kbd>Space</kbd>" : "Play <kbd>Space</kbd>";
    el.querySelectorAll("button, input, select").forEach((x) => (x.disabled = rendering));
    if (b.mode === "review" && st._wasPlaying) st.arrowEd?.moved();
    st._wasPlaying = b.mode === "playing";
    updateProgress();
  }

  function buildFilm(film, dur) {
    const n = 10;
    let html = "";
    for (let i = 0; i < n; i++) {
      const url = snapAt((dur * (i + 0.5)) / n);
      html += url ? `<img src="${url}" alt="">` : `<span></span>`;
    }
    film.innerHTML = html;
    // pictures are still arriving right after stopping; fill in any blanks shortly after
    if (film.querySelector("span")) setTimeout(() => film.isConnected && buildFilm(film, dur), 400);
  }

  function localTrim(start, end, at) {
    const el = st.dom?.root.querySelector("#review");
    if (!el || el.hidden) return;
    const dur = st.blender?.take?.duration || 1;
    const sel = el.querySelector("#sel");
    sel.style.left = `${(start / dur) * 100}%`;
    sel.style.width = `${((end - start) / dur) * 100}%`;
    el.querySelector("#times").innerHTML =
      `<span>Start ${start.toFixed(2)} s</span><span>Length ${(end - start).toFixed(2)} s</span><span>End ${end.toFixed(2)} s</span>`;
    const url = snapAt(at);
    const img = el.querySelector("#snap");
    if (url && img.getAttribute("src") !== url) img.src = url;
    el.querySelector("#snap-cap").textContent = `Your camera at ${at.toFixed(2)} s`;
  }

  function updateTimeline() {
    const el = st.dom?.root.querySelector("#review");
    if (!el || el.hidden) return;
    const dur = st.blender?.take?.duration || 1;
    const ph = el.querySelector("#ph");
    ph.hidden = st.playhead == null;
    if (st.playhead != null) {
      ph.style.left = `${(st.playhead / dur) * 100}%`;
      const url = snapAt(st.playhead);
      if (url) el.querySelector("#snap").src = url;
      el.querySelector("#snap-cap").textContent = `Your camera at ${st.playhead.toFixed(2)} s`;
    }
  }

  function updateProgress() {
    const el = st.dom?.root.querySelector("#review");
    if (!el) return;
    const p = st.blender?.progress;
    const bar = el.querySelector("#gif-prog");
    const txt = el.querySelector("#gif-prog-text");
    if (!bar) return;
    bar.hidden = !p;
    if (p) {
      const frac = p.stage === "encode" ? 1 : p.total ? p.done / p.total : 0;
      bar.querySelector("i").style.width = `${Math.round(frac * 100)}%`;
      txt.textContent = p.stage === "encode" ? "Building the GIF..." : `Rendering frame ${p.done} of ${p.total} in Blender...`;
    } else {
      txt.textContent = st.blender?.error || "";
    }
  }

  // connect automatically; the link retries quietly until Blender is listening
  link.connect(st.settings.port);
  return { mount, link, st };
}

let keyHandler = null;
document.addEventListener("keydown", (e) => keyHandler && keyHandler(e));
