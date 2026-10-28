// One Euro filter (Casiez et al. 2012): smooths jitter when still, follows quickly when moving.
// Each point is filtered as a whole vector, so its speed sets how much it is smoothed.

function alpha(cutoff, dt) {
  const tau = 1 / (2 * Math.PI * cutoff);
  return 1 / (1 + tau / dt);
}

export class VectorEuro {
  constructor(minCutoff, beta, dCutoff = 1.0) {
    this.minCutoff = minCutoff;
    this.beta = beta;
    this.dCutoff = dCutoff;
    this.reset();
  }

  reset() {
    this.x = null;
    this.speed = 0;
    this.t = null;
  }

  filter(v, t) {
    if (this.x === null || this.x.length !== v.length) {
      this.x = v.slice();
      this.t = t;
      this.speed = 0;
      return this.x;
    }
    const dt = Math.min(0.5, Math.max(1e-3, t - this.t));
    this.t = t;
    let d2 = 0;
    for (let i = 0; i < v.length; i++) d2 += (v[i] - this.x[i]) ** 2;
    const rawSpeed = Math.sqrt(d2) / dt;
    this.speed += alpha(this.dCutoff, dt) * (rawSpeed - this.speed);
    const a = alpha(this.minCutoff + this.beta * this.speed, dt);
    for (let i = 0; i < v.length; i++) this.x[i] += a * (v[i] - this.x[i]);
    return this.x;
  }
}

/** A set of filters, one per key, sharing the same settings. */
export class FilterGroup {
  constructor(minCutoff, beta) {
    this.minCutoff = minCutoff;
    this.beta = beta;
    this.filters = new Map();
    this.enabled = true;
  }

  setMinCutoff(c) {
    this.minCutoff = c;
    for (const f of this.filters.values()) f.minCutoff = c;
  }

  filter(key, v, t) {
    if (!this.enabled) return v;
    let f = this.filters.get(key);
    if (!f) {
      f = new VectorEuro(this.minCutoff, this.beta);
      this.filters.set(key, f);
    }
    return f.filter(v, t).slice();
  }

  reset(prefix) {
    if (prefix === undefined) this.filters.clear();
    else for (const k of [...this.filters.keys()]) if (String(k).startsWith(prefix)) this.filters.delete(k);
  }
}
