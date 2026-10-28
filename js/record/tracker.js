// Runs the landmarkers on each new camera frame and turns the results into the small
// message the Blender add-on expects. Built for low delay:
//   * requestVideoFrameCallback runs as soon as the camera delivers a frame (no polling),
//   * the models run in VIDEO mode, so hands are tracked between frames instead of re-detected,
//   * the face (expressions change more slowly) can run every 2nd or 3rd frame,
//   * a One Euro filter removes jitter without adding lag to quick movements.

import { FilterGroup } from "./oneeuro.js";

const FACE_POINTS = ["33", "263", "10", "152", "1"];          // eye corners, forehead, chin, nose tip
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
    // smoothing (minCutoff in Hz is set from the Smoothing slider)
    this.filters = {
      pose: new FilterGroup(1.8, 8),        // meters
      hand: new FilterGroup(1.8, 8),        // meters
      face: new FilterGroup(1.8, 0.03),     // pixels
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
                  pose: null, hands: [], face: null };

    let poseImg = null;
    if (poseRes && poseRes.worldLandmarks && poseRes.worldLandmarks.length) {
      const w = poseRes.worldLandmarks[0];
      poseImg = poseRes.landmarks[0];
      msg.pose = w.map((p, i) => {
        const vis = poseImg[i].visibility ?? p.visibility ?? 1;
        const f = F.pose.filter(i, [p.x, p.y, p.z], t);
        return [round(f[0]), round(f[1]), round(f[2]), Math.round(vis * 100) / 100];
      });
    } else {
      F.pose.reset();
    }

    if (handRes && handRes.worldLandmarks) {
      const sides = assignSides(handRes, poseImg);
      const present = { R: false, L: false };
      handRes.worldLandmarks.forEach((world, i) => {
        const side = sides[i];
        if (!side || present[side]) return;
        present[side] = true;
        if (performance.now() - this.handSeen[side] > 250) F.hand.reset(side);   // reappeared: start fresh
        this.handSeen[side] = performance.now();
        msg.hands.push({
          side,
          score: Math.round((handRes.handedness?.[i]?.[0]?.score ?? 1) * 100) / 100,
          world: world.map((p, j) => F.hand.filter(side + j, [p.x, p.y, p.z], t).map(round)),
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
 * Decide which hand is the person's right and which is the left.
 * MediaPipe's own left/right label assumes a mirrored selfie image and is unreliable,
 * so each hand is matched to the nearest wrist of the body pose instead.
 */
export function assignSides(handRes, poseImg) {
  const hands = handRes.landmarks || [];
  const n = hands.length;
  const out = new Array(n).fill(null);
  if (!n) return out;
  const wrist = (i) => hands[i][0];
  const pw = poseImg && poseImg[16] && poseImg[15] ? { R: poseImg[16], L: poseImg[15] } : null;
  if (pw) {
    const d = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    if (n === 1) {
      out[0] = d(wrist(0), pw.R) <= d(wrist(0), pw.L) ? "R" : "L";
    } else {
      const keep = d(wrist(0), pw.R) + d(wrist(1), pw.L);
      const swap = d(wrist(0), pw.L) + d(wrist(1), pw.R);
      out[0] = keep <= swap ? "R" : "L";
      out[1] = keep <= swap ? "L" : "R";
    }
    return out;
  }
  // No body pose: fall back to MediaPipe's label, flipped because the camera image isn't mirrored.
  for (let i = 0; i < n; i++) {
    const label = handRes.handedness?.[i]?.[0]?.categoryName;
    out[i] = label === "Left" ? "R" : label === "Right" ? "L" : null;
  }
  if (n === 2 && out[0] === out[1]) {
    // both got the same label: the one further to the image's left is the person's right
    const left0 = wrist(0).x < wrist(1).x;
    out[0] = left0 ? "R" : "L";
    out[1] = left0 ? "L" : "R";
  }
  return out;
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
    for (const k of [33, 263, 10, 152, 1, 13, 14, 61, 291, 105, 334]) {
      g.beginPath();
      g.arc(face[k].x * W, face[k].y * H, Math.max(1.5, W / 320), 0, Math.PI * 2);
      g.fill();
    }
  }
}
