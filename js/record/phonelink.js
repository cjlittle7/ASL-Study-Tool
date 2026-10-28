// Phone as a second camera: pairing and the data link between the phone and this laptop page.
//
// Pairing uses PeerJS's free public server only to introduce the two browsers (a short code).
// After that they talk directly (WebRTC), usually over your own Wi-Fi. No video is sent: the
// phone tracks itself and sends about 3 KB of point positions per frame.
//
// Two channels:
//   * "control" (reliable): clock sync pings, status.
//   * "frames" (unordered, never re-sent): tracking frames. A late frame is useless, so it is
//     dropped instead of holding up the newer ones behind it. This keeps the delay low.
//
// Keep this in mind for later: a second USB webcam on the laptop would skip all of this and feed
// the same "cam2" messages straight from a second Tracker on this page.

const PEERJS_URL = "https://cdn.jsdelivr.net/npm/peerjs@1.5.5/dist/peerjs.min.js";
const ID_PREFIX = "asl-study-tool-";
const FRAMES_CHANNEL_ID = 7;           // both sides open the same pre-agreed channel

let peerjsLoading = null;
function loadPeerJS() {
  if (window.Peer) return Promise.resolve(window.Peer);
  if (!peerjsLoading) {
    peerjsLoading = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = PEERJS_URL;
      s.onload = () => (window.Peer ? resolve(window.Peer) : reject(new Error("PeerJS did not load")));
      s.onerror = () => {
        peerjsLoading = null;
        reject(new Error("Couldn't load the pairing library. Is this device online?"));
      };
      document.head.appendChild(s);
    });
  }
  return peerjsLoading;
}

/** Options for new Peer(): the public server, or ?peer=host:port for a self-hosted one (tests). */
function peerOptions() {
  const m = /[?&]peer=([^&#]+)/.exec(location.href);
  if (!m) return { debug: 0 };
  const [host, port] = decodeURIComponent(m[1]).split(":");
  return { host, port: +port || 9000, path: "/", secure: false, debug: 0, config: { iceServers: [] } };
}

export function newCode() {
  const abc = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";       // no 0/O or 1/I mix-ups
  let s = "";
  const r = crypto.getRandomValues(new Uint8Array(6));
  for (const x of r) s += abc[x % abc.length];
  return s;
}

function openFramesChannel(pc) {
  return pc.createDataChannel("frames", { negotiated: true, id: FRAMES_CHANNEL_ID, ordered: false, maxRetransmits: 0 });
}

/**
 * Laptop side: waits for the phone, keeps the clocks in step, and hands each phone frame (with its
 * time converted to this page's clock) to onFrame.
 */
export class PhoneHub {
  constructor({ onFrame, onStatus }) {
    this.onFrame = onFrame;
    this.onStatus = onStatus;
    this.code = null;
    this.peer = null;
    this.conn = null;
    this.frames = null;
    this.state = "off";            // off, starting, waiting, connected, error
    this.error = "";
    this.offset = 0;               // phone clock minus this page's clock (ms)
    this.rtt = 0;
    this.syncs = [];
    this.fps = 0;
    this._times = [];
    this.lastFrameAt = 0;
    this.phoneMs = 0;
  }

  async start(code) {
    this.stop();
    this.code = code || newCode();
    this._set("starting");
    let Peer;
    try {
      Peer = await loadPeerJS();
    } catch (err) {
      this._set("error", err.message);
      return;
    }
    const peer = new Peer(ID_PREFIX + this.code, peerOptions());
    this.peer = peer;
    peer.on("open", () => this._set("waiting"));
    peer.on("error", (err) => {
      if (err.type === "unavailable-id") this.start();                     // code taken: pick another
      else this._set("error", `Pairing problem: ${err.type || err.message}`);
    });
    peer.on("disconnected", () => {
      if (this.peer === peer && !peer.destroyed) peer.reconnect();          // keep the code reachable
    });
    peer.on("connection", (conn) => this._accept(conn));
  }

  _accept(conn) {
    if (this.conn) this.conn.close();
    this.conn = conn;
    conn.on("open", () => {
      this.frames = openFramesChannel(conn.peerConnection);
      this.frames.onmessage = (e) => this._onFrame(e.data);
      this.syncs = [];
      this._set("connected");
      this._ping();
    });
    conn.on("data", (m) => this._onControl(m));
    conn.on("close", () => {
      if (this.conn === conn) {
        this.conn = null;
        this.frames = null;
        this._set(this.peer ? "waiting" : "off");
      }
    });
    conn.on("error", () => {});
  }

  _ping() {
    clearTimeout(this._pingTimer);
    if (!this.conn || !this.conn.open) return;
    this.conn.send({ type: "ping", a: performance.now() });
    // quick pings at first to lock the clock offset in, then once a second
    this._pingTimer = setTimeout(() => this._ping(), this.syncs.length < 15 ? 150 : 1000);
  }

  _onControl(m) {
    if (!m || typeof m !== "object") return;
    if (m.type === "pong") {
      const c = performance.now();
      const rtt = c - m.a;
      this.syncs.push({ rtt, off: m.b - (m.a + c) / 2 });
      if (this.syncs.length > 30) this.syncs.shift();
      // the fastest round trips give the best estimate of the clock difference
      const best = [...this.syncs].sort((x, y) => x.rtt - y.rtt).slice(0, 5);
      this.offset = best.reduce((s, x) => s + x.off, 0) / best.length;
      this.rtt = best[0].rtt;
    } else if (m.type === "status") {
      this.phoneMs = m.ms || 0;
    }
  }

  _onFrame(data) {
    let msg;
    try {
      msg = JSON.parse(data);
    } catch {
      return;
    }
    if (!this.syncs.length) return;           // can't place it in time yet
    msg.type = "cam2";
    msg.t = Math.round((msg.t - this.offset) * 100) / 100;    // into this page's clock
    const now = performance.now();
    this.lastFrameAt = now;
    this.delay = now - msg.t;                 // phone camera -> here (ms)
    this._times.push(now);
    while (this._times.length && now - this._times[0] > 1000) this._times.shift();
    this.fps = this._times.length;
    this.onFrame(msg);
  }

  get receiving() {
    return this.state === "connected" && performance.now() - this.lastFrameAt < 1000;
  }

  stop() {
    clearTimeout(this._pingTimer);
    if (this.conn) this.conn.close();
    if (this.peer) this.peer.destroy();
    this.conn = this.peer = this.frames = null;
    this._set("off");
  }

  _set(state, error = "") {
    this.state = state;
    this.error = error;
    this.onStatus?.(state, error);
  }
}

/** Phone side: connects to the laptop's code and sends tracking frames. */
export class PhoneSender {
  constructor({ onStatus }) {
    this.onStatus = onStatus;
    this.peer = null;
    this.conn = null;
    this.frames = null;
    this.state = "off";
    this.sent = 0;
    this.dropped = 0;
  }

  async connect(code) {
    this.close();
    this._set("connecting");
    let Peer;
    try {
      Peer = await loadPeerJS();
    } catch (err) {
      this._set("error", err.message);
      return;
    }
    const peer = new Peer(peerOptions());
    this.peer = peer;
    peer.on("error", (err) => {
      const why = err.type === "peer-unavailable"
        ? "The laptop wasn't found. Check the code, and that the Record tab on the laptop is showing it."
        : `Pairing problem: ${err.type || err.message}`;
      this._set("error", why);
    });
    peer.on("open", () => {
      const conn = peer.connect(ID_PREFIX + code.toUpperCase(), { serialization: "json", reliable: true });
      this.conn = conn;
      conn.on("open", () => {
        this.frames = openFramesChannel(conn.peerConnection);
        this.frames.bufferedAmountLowThreshold = 0;
        this._set("connected");
      });
      conn.on("data", (m) => {
        if (m && m.type === "ping") conn.send({ type: "pong", a: m.a, b: performance.now() });
      });
      conn.on("close", () => this._set("closed", "The laptop closed the connection."));
      conn.on("error", () => {});
    });
  }

  /** Send one frame now, or drop it if the link is still busy with the last ones. */
  send(msg) {
    const ch = this.frames;
    if (!ch || ch.readyState !== "open") return false;
    if (ch.bufferedAmount > 8000) {
      this.dropped++;
      return false;
    }
    ch.send(JSON.stringify(msg));
    this.sent++;
    return true;
  }

  status(extra) {
    if (this.conn && this.conn.open) this.conn.send({ type: "status", ...extra });
  }

  close() {
    if (this.conn) this.conn.close();
    if (this.peer) this.peer.destroy();
    this.conn = this.peer = this.frames = null;
    this._set("off");
  }

  _set(state, error = "") {
    this.state = state;
    this.error = error;
    this.onStatus?.(state, error);
  }
}
