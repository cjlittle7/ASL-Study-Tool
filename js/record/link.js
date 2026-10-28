// WebSocket connection to the Blender add-on (Live Tracking & Recording panel).
// Only small JSON messages travel here: about 4 KB of landmark numbers per frame.

export class BlenderLink {
  constructor({ onMessage, onBinary, onStatus }) {
    this.onMessage = onMessage;
    this.onBinary = onBinary;
    this.onStatus = onStatus;
    this.ws = null;
    this.port = 8766;
    this.want = false;
    this.connected = false;
    this.sent = new Map();          // frame id -> { capture, sent }
    this.latency = { total: 0, blender: 0 };
    this.dropped = 0;
    this._timer = null;
  }

  connect(port) {
    this.port = port;
    this.want = true;
    this._open();
  }

  disconnect() {
    this.want = false;
    clearTimeout(this._timer);
    if (this.ws) this.ws.close();
  }

  _open() {
    clearTimeout(this._timer);
    if (this.ws && (this.ws.readyState === 0 || this.ws.readyState === 1)) return;
    let ws;
    try {
      ws = new WebSocket(`ws://127.0.0.1:${this.port}`);
    } catch (err) {
      this.onStatus("error", String(err));
      return;
    }
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    this.onStatus("connecting");
    ws.onopen = () => {
      this.connected = true;
      this.sent.clear();
      this.onStatus("open");
      this.send({ type: "hello", app: "asl-study-tool", protocol: 1 });
    };
    ws.onclose = () => {
      const was = this.connected;
      this.connected = false;
      if (this.ws === ws) this.ws = null;
      this.onStatus(was ? "closed" : "unreachable");
      if (this.want) this._timer = setTimeout(() => this._open(), 1500);
    };
    ws.onerror = () => {};
    ws.onmessage = (e) => {
      if (typeof e.data !== "string") {
        this.onBinary(e.data);
        return;
      }
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg.type === "ack") this._ack(msg);
      else this.onMessage(msg);
    };
  }

  send(obj) {
    if (this.ws && this.ws.readyState === 1) {
      this.ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  /** Send a frame unless the connection is backed up (then the newest frame wins next time). */
  sendFrame(msg) {
    if (!this.ws || this.ws.readyState !== 1) return false;
    if (this.ws.bufferedAmount > 32 * 1024) {
      this.dropped++;
      return false;
    }
    this.ws.send(JSON.stringify(msg));
    this.sent.set(msg.id, { capture: msg.t, sent: performance.now() });
    if (this.sent.size > 120) this.sent.delete(this.sent.keys().next().value);
    return true;
  }

  _ack(msg) {
    if (msg.touch) this.touch = msg.touch;
    const s = this.sent.get(msg.id);
    if (!s) return;
    this.sent.delete(msg.id);
    const now = performance.now();
    const ema = (old, v) => (old ? old * 0.85 + v * 0.15 : v);
    this.latency.total = ema(this.latency.total, now - s.capture);
    this.latency.blender = ema(this.latency.blender, now - s.sent);
  }
}
