// Runs the landmarkers on each new camera frame and turns the results into the small
// message the Blender add-on expects. Built for low delay:
//   * requestVideoFrameCallback runs as soon as the camera delivers a frame (no polling),
//   * the models run in VIDEO mode, so hands are tracked between frames instead of re-detected,
//   * the face (expressions change more slowly) can run every 2nd or 3rd frame,
//   * a One Euro filter removes jitter without adding lag to quick movements.

import { FilterGroup } from "./oneeuro.js";

// Face points sent to Blender, in pixels. They give the head's rotation, the eyebrows, and
// "anchors" on the face (nose, chin, cheeks, temples...) so a hand that touches the face lands
// on the same spot on the figure's face. Keep in sync with FACE_ANCHORS in the add-on.
const FACE_POINTS = [
  "1", "4", "6", "168", "151", "10", "152", "199", "17", "14", "13", "0", "61", "291",
  "33", "133", "263", "362", "159", "145", "386", "374",
  "70", "105", "107", "300", "334", "336",
  "98", "327", "50", "280", "234", "454", "127", "356", "172", "397", "58", "288",
];
const BLEND_KEYS = [
  "browInnerUp", "browOuterUpLeft", "browOuterUpRight", "browDownLeft", "browDownRight",
  "jawOpen", "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft", "mouthFrownRight",
  "mouthPucker", "mouthFunnel", "cheekPuff", "eyeBlinkLeft", "eyeBlinkRight", "eyeSquintLeft", "eyeSquintRight",
];
const round = (v) => Math.round(v * 1e4) / 1e4;

export class Tracker {
  constructor(video, landmarkers, onFrame) {
    this.video = video;
    this.lm = landmarkers;
    this.onFrame = onFrame;
    this.running = false;
    this.id = 0;
    this.lastTs = 0;
    this.faceEvery = 1;            // 0 = off, 1 = every frame, 2 = every 2nd frame...
    this.autoFace = true;
    this.frameCount = 0;
    this.lastFace = null;
    this.detectMs = 0;
    this.handSeen = { R: 0, L: 0 };
    this.sideAssigner = new SideAssigner();
    this.lastSides = null;
    // smoothing (minCutoff in Hz is set from the Smoothing slider)
    this.filters = {
      pose: new FilterGroup(1.8, 8),        // meters
      hand: new FilterGroup(1.8, 8),        // meters
      face: new FilterGroup(1.8, 0.03),     // pixels
      img: new FilterGroup(1.8, 0.03),      // pixels (body and hand points in the picture)
      blend: new FilterGroup(1.8, 2),       // 0..1
    };
    this._tick = this._tick.bind(this);
  }

  setSmoothing(s) {
    // s: 0 = off, 1 = strongest. Maps to a minimum cutoff between ~10 Hz and ~0.3 Hz.
    const off = s <= 0.001;
    const c = Math.pow(10, 1 - 1.5 * s);
    for (const g of Object.values(this.filters)) {
      g.enabled = !off;
      g.setMinCutoff(c);
    }
  }

  start() {
    if (this.running) return;
    this.running = true;
    this._schedule();
  }

  stop() {
    this.running = false;
  }

  _schedule() {
    if (!this.running) return;
    if (this.video.requestVideoFrameCallback) this.video.requestVideoFrameCallback(this._tick);
    else requestAnimationFrame(() => this._tick(performance.now(), null));
  }

  _tick(now, meta) {
    if (!this.running) return;
    const v = this.video;
    if (v.readyState < 2 || !v.videoWidth) {
      this._schedule();
      return;
    }
    if (!meta && v.currentTime === this._lastVideoTime) {
      this._schedule();
      return;
    }
    this._lastVideoTime = v.currentTime;
    const capture = (meta && (meta.captureTime || meta.presentationTime)) || performance.now();
    const t0 = performance.now();
    const ts = Math.max(t0, this.lastTs + 1);           // timestamps must always increase
    this.lastTs = ts;
    let handRes = null, poseRes = null, faceRes = null;
    try {
      handRes = this.lm.hand.detectForVideo(v, ts);
      poseRes = this.lm.pose.detectForVideo(v, ts);
      this.frameCount++;
      if (this.faceEvery > 0 && this.frameCount % this.faceEvery === 0) {
        faceRes = this.lm.face.detectForVideo(v, ts);
        this.lastFace = faceRes;
      }
    } catch (err) {
      console.error(err);
    }
    const ms = performance.now() - t0;
    this.detectMs = this.detectMs ? this.detectMs * 0.9 + ms * 0.1 : ms;
    if (this.autoFace && this.faceEvery > 0) {
      this.faceEvery = this.detectMs > 45 ? 3 : this.detectMs > 28 ? 2 : 1;
    }
    const msg = this._build(handRes, poseRes, faceRes, capture, v.videoWidth, v.videoHeight, ms);
    try {
      this.onFrame(msg, { handRes, poseRes, faceRes: faceRes || this.lastFace, ms });
    } finally {
      this._schedule();
    }
  }

  _build(handRes, poseRes, faceRes, capture, W, H, ms) {
    const t = capture / 1000;
    const F = this.filters;
    const msg = { type: "frame", id: ++this.id, t: Math.round(capture * 100) / 100, dt: Math.round(ms * 10) / 10,
                  img: [W, H], pose: null, pose2d: null, hands: [], face: null };
    const px = (v) => Math.round(v * 10) / 10;

    let poseImg = null;
    if (poseRes && poseRes.worldLandmarks && poseRes.worldLandmarks.length) {
      const w = poseRes.worldLandmarks[0];
      poseImg = poseRes.landmarks[0];
      msg.pose = w.map((p, i) => {
        const vis = poseImg[i].visibility ?? p.visibility ?? 1;
        const f = F.pose.filter(i, [p.x, p.y, p.z], t);
        return [round(f[0]), round(f[1]), round(f[2]), Math.round(vis * 100) / 100];
      });
      // where each body point is in the picture: used to work out how far away things are
      msg.pose2d = poseImg.map((p, i) => F.img.filter("p" + i, [p.x * W, p.y * H], t).map(px));
    } else {
      F.pose.reset();
      F.img.reset("p");
    }

    if (handRes && handRes.worldLandmarks) {
      const sides = this.sideAssigner.assign(handRes, poseImg);
      this.lastSides = sides;
      const present = { R: false, L: false };
      handRes.worldLandmarks.forEach((world, i) => {
        const side = sides[i];
        if (!side || present[side]) return;
        present[side] = true;
        if (performance.now() - this.handSeen[side] > 250) {      // reappeared: start fresh
          F.hand.reset(side);
          F.img.reset(side);
        }
        this.handSeen[side] = performance.now();
        const img = handRes.landmarks[i];
        msg.hands.push({
          side,
          score: Math.round((handRes.handedness?.[i]?.[0]?.score ?? 1) * 100) / 100,
          world: world.map((p, j) => F.hand.filter(side + j, [p.x, p.y, p.z], t).map(round)),
          img: img.map((p, j) => F.img.filter(side + j, [p.x * W, p.y * H], t).map(px)),
        });
      });
    }

    if (faceRes && faceRes.faceLandmarks && faceRes.faceLandmarks.length) {
      const L = faceRes.faceLandmarks[0];
      const pts = {};
      for (const k of FACE_POINTS) {
        const p = L[+k];
        pts[k] = F.face.filter(k, [p.x * W, p.y * H, p.z * W], t).map((x) => Math.round(x * 10) / 10);
      }
      const blend = {};
      const cats = faceRes.faceBlendshapes?.[0]?.categories || [];
      const byName = Object.fromEntries(cats.map((c) => [c.categoryName, c.score]));
      for (const k of BLEND_KEYS) {
        if (k in byName) blend[k] = Math.round(F.blend.filter(k, [byName[k]], t)[0] * 1000) / 1000;
      }
      msg.face = { pts, blend };
    }
    return msg;
  }
}

/**
 * Decide which hand is the person's right and which is the left, frame after frame.
 * Three clues are weighed:
 *   * where each side's hand was a moment ago (hands don't jump), which keeps crossed arms
 *     straight even when the body tracker mixes up the wrists,
 *   * which body-pose wrist the hand is nearest,
 *   * MediaPipe's own left/right label (it judges from the hand's shape; flipped because the
 *     camera picture isn't mirrored).
 * A swap must be clearly better than keeping the current labels, so one odd frame changes nothing.
 */
export class SideAssigner {
  constructor() {
    this.prev = { R: null, L: null };
  }

  assign(handRes, poseImg, now = performance.now()) {
    const hands = handRes?.landmarks || [];
    const n = hands.length;
    const out = new Array(n).fill(null);
    if (!n) return out;
    const info = hands.map((lm, i) => {
      let x = 0, y = 0;
      for (const p of lm) { x += p.x; y += p.y; }
      const size = Math.hypot(lm[0].x - lm[9].x, lm[0].y - lm[9].y) || 0.05;
      const label = handRes.handedness?.[i]?.[0];
      return { c: { x: x / lm.length, y: y / lm.length }, w: lm[0], size,
               label: label ? (label.categoryName === "Left" ? "R" : "L") : null, conf: label?.score ?? 0 };
    });
    const pw = poseImg && poseImg[16] && poseImg[15] &&
      (poseImg[16].visibility ?? 1) > 0.3 && (poseImg[15].visibility ?? 1) > 0.3 ? { R: poseImg[16], L: poseImg[15] } : null;
    const d = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    const cost = (h, s) => {
      let c = 0;
      const pv = this.prev[s];
      if (pv && now - pv.t < 400) c += 1.2 * Math.min(3, d(h.c, pv.c) / h.size);
      if (pw) c += 0.6 * Math.min(3, d(h.w, pw[s]) / h.size);
      if (h.label) c += 0.8 * h.conf * (h.label === s ? 0 : 1);
      return c;
    };
    if (n === 1) {
      out[0] = cost(info[0], "R") <= cost(info[0], "L") ? "R" : "L";
    } else {
      const keep = cost(info[0], "R") + cost(info[1], "L");
      const swap = cost(info[0], "L") + cost(info[1], "R");
      // "keep" means: hand 0 is the one that was the right hand last frame, if it was
      const was0 = this.prev.R && this.prev.L ? (d(info[0].c, this.prev.R.c) <= d(info[0].c, this.prev.L.c) ? "R" : "L") : null;
      let first;
      if (was0 === "R") first = swap < keep - 0.5 ? "L" : "R";
      else if (was0 === "L") first = keep < swap - 0.5 ? "R" : "L";
      else first = keep <= swap ? "R" : "L";
      out[0] = first;
      out[1] = first === "R" ? "L" : "R";
    }
    out.forEach((s, i) => {
      if (s) this.prev[s] = { c: info[i].c, t: now };
    });
    return out;
  }
}

/** One-off version (no memory of earlier frames). */
export function assignSides(handRes, poseImg) {
  return new SideAssigner().assign(handRes, poseImg);
}

// --- drawing the tracked points over the camera picture ---------------------------

const HAND_LINKS = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11],
  [11, 12], [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17]];
const ARM_LINKS = [[11, 12], [11, 13], [13, 15], [12, 14], [14, 16], [11, 23], [12, 24]];

export function drawOverlay(canvas, video, res, sides) {
  const W = video.videoWidth, H = video.videoHeight;
  if (canvas.width !== W) canvas.width = W;
  if (canvas.height !== H) canvas.height = H;
  const g = canvas.getContext("2d");
  g.clearRect(0, 0, W, H);
  g.lineCap = "round";
  const pose = res.poseRes?.landmarks?.[0];
  if (pose) {
    g.strokeStyle = "rgba(255,255,255,0.85)";
    g.lineWidth = Math.max(2, W / 220);
    for (const [a, b] of ARM_LINKS) {
      if ((pose[a].visibility ?? 1) < 0.5 || (pose[b].visibility ?? 1) < 0.5) continue;
      g.beginPath();
      g.moveTo(pose[a].x * W, pose[a].y * H);
      g.lineTo(pose[b].x * W, pose[b].y * H);
      g.stroke();
    }
  }
  const hands = res.handRes?.landmarks || [];
  hands.forEach((lm, i) => {
    const side = sides ? sides[i] : null;
    g.strokeStyle = side === "L" ? "#ffd23f" : "#4fd1a5";
    g.fillStyle = g.strokeStyle;
    g.lineWidth = Math.max(2, W / 260);
    for (const [a, b] of HAND_LINKS) {
      g.beginPath();
      g.moveTo(lm[a].x * W, lm[a].y * H);
      g.lineTo(lm[b].x * W, lm[b].y * H);
      g.stroke();
    }
    for (const p of lm) {
      g.beginPath();
      g.arc(p.x * W, p.y * H, Math.max(2, W / 200), 0, Math.PI * 2);
      g.fill();
    }
  });
  const face = res.faceRes?.faceLandmarks?.[0];
  if (face) {
    g.fillStyle = "rgba(255,255,255,0.9)";
    for (const k of FACE_POINTS) {
      g.beginPath();
      g.arc(face[+k].x * W, face[+k].y * H, Math.max(1.5, W / 320), 0, Math.PI * 2);
      g.fill();
    }
  }
}
