// Line charts for simulation results: waveforms against time, or gain against frequency (log).
"use strict";

class Plot {
  constructor(host, x, series, opts = {}) {
    this.host = host; this.x = x; this.series = series; this.opts = opts;
    this.hidden = new Set();
    this.canvas = document.createElement("canvas");
    this.canvas.className = "plot";
    this.legend = document.createElement("div");
    this.legend.className = "plot-legend";
    this.tip = document.createElement("div");
    this.tip.className = "plot-tip";
    this.tip.hidden = true;
    host.append(this.legend, this.canvas, this.tip);
    this.canvas.addEventListener("mousemove", e => this.hover(e));
    this.canvas.addEventListener("mouseleave", () => { this.tip.hidden = true; this.draw(); });
    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(host);
    window.addEventListener("wb-theme", () => { this.makeLegend(); this.draw(); });
    window.addEventListener("wb-zoom", () => this.draw());
    this.makeLegend();
  }

  colours() {
    const cs = getComputedStyle(document.documentElement);
    return ["--accent", "--accent-2", "--info", "--warn", "--ok", "--danger", "--text", "--muted"]
      .map(v => cs.getPropertyValue(v).trim() || "#888");
  }

  makeLegend() {
    const cols = this.colours();
    this.legend.innerHTML = "";
    this.series.forEach((s, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip toggle" + (this.hidden.has(i) ? "" : " on");
      b.style.setProperty("--dot", cols[i % cols.length]);
      b.textContent = s.name;
      b.onclick = () => { this.hidden.has(i) ? this.hidden.delete(i) : this.hidden.add(i); this.makeLegend(); this.draw(); };
      this.legend.append(b);
    });
  }

  tx(v) { return this.opts.logx ? Math.log10(Math.max(v, 1e-12)) : v; }

  draw(cursor) {
    const c = this.canvas, dpr = (window.devicePixelRatio || 1) * wbZoom();
    const w = this.host.clientWidth || 600, h = this.opts.height || 260;
    c.width = w * dpr; c.height = h * dpr; c.style.width = w + "px"; c.style.height = h + "px";
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const cs = getComputedStyle(document.documentElement);
    const text = cs.getPropertyValue("--muted").trim(), line = cs.getPropertyValue("--line").trim();
    const cols = this.colours();
    const L = 52, R = 12, T = 10, B = 28;
    const xs = this.x.map(v => this.tx(v));
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    let y0 = Infinity, y1 = -Infinity;
    this.series.forEach((s, i) => { if (!this.hidden.has(i)) for (const v of s.values) { if (v < y0) y0 = v; if (v > y1) y1 = v; } });
    if (!isFinite(y0)) { y0 = 0; y1 = 1; }
    if (y1 - y0 < 1e-9) { y0 -= 0.5; y1 += 0.5; }
    const pad = (y1 - y0) * 0.06; y0 -= pad; y1 += pad;
    const X = v => L + (v - x0) / ((x1 - x0) || 1) * (w - L - R);
    const Y = v => T + (1 - (v - y0) / (y1 - y0)) * (h - T - B);
    this.map = { X, Y, xs, L, R, w };
    g.clearRect(0, 0, w, h);
    g.font = "11px system-ui, sans-serif"; g.fillStyle = text; g.strokeStyle = line; g.lineWidth = 1;
    for (const t of ticks(y0, y1, 5)) {
      g.beginPath(); g.moveTo(L, Y(t)); g.lineTo(w - R, Y(t)); g.stroke();
      g.textAlign = "right"; g.fillText(fmt(t), L - 6, Y(t) + 4);
    }
    const xt = this.opts.logx ? logTicks(x0, x1) : ticks(x0, x1, 6);
    for (const t of xt) {
      g.beginPath(); g.moveTo(X(t), T); g.lineTo(X(t), h - B); g.stroke();
      g.textAlign = "center"; g.fillText(this.opts.logx ? fmt(10 ** t) : fmtTime(t, x1), X(t), h - B + 16);
    }
    g.textAlign = "left"; g.fillText(this.opts.ylabel || "", L + 4, T + 12);
    g.textAlign = "right"; g.fillText(this.opts.xlabel || "", w - R, h - 4);
    this.series.forEach((s, i) => {
      if (this.hidden.has(i)) return;
      g.strokeStyle = cols[i % cols.length]; g.lineWidth = 1.6; g.beginPath();
      s.values.forEach((v, k) => { const px = X(xs[k]), py = Y(v); k ? g.lineTo(px, py) : g.moveTo(px, py); });
      g.stroke();
    });
    if (cursor !== undefined) {
      g.strokeStyle = text; g.setLineDash([3, 3]); g.beginPath(); g.moveTo(X(xs[cursor]), T); g.lineTo(X(xs[cursor]), h - B); g.stroke(); g.setLineDash([]);
    }
  }

  hover(e) {
    if (!this.map) return;
    const r = this.canvas.getBoundingClientRect(), px = (e.clientX - r.left) / wbZoom();
    const { xs, X } = this.map;
    let k = 0, best = Infinity;
    for (let i = 0; i < xs.length; i++) { const d = Math.abs(X(xs[i]) - px); if (d < best) { best = d; k = i; } }
    this.draw(k);
    const rows = this.series.map((s, i) => this.hidden.has(i) ? "" : `${s.name}: ${fmt(s.values[k])}`).filter(Boolean);
    this.tip.textContent = `${this.opts.logx ? fmt(this.x[k]) + " Hz" : fmtTime(this.x[k], this.x[this.x.length - 1])}\n${rows.join("\n")}`;
    this.tip.hidden = false;
    this.tip.style.left = Math.min(px + 14, r.width - 180) + "px";
    this.tip.style.top = "40px";
  }
}

function ticks(a, b, n) {
  const span = b - a, step0 = span / n, mag = 10 ** Math.floor(Math.log10(step0 || 1));
  const step = [1, 2, 5, 10].map(m => m * mag).find(s => span / s <= n) || mag * 10;
  const out = [];
  for (let t = Math.ceil(a / step) * step; t <= b + 1e-12; t += step) out.push(+t.toPrecision(12));
  return out;
}
function logTicks(a, b) { const out = []; for (let t = Math.ceil(a); t <= b; t++) out.push(t); return out; }
function fmt(v) {
  const a = Math.abs(v);
  if (a >= 1e6) return (v / 1e6).toPrecision(3) + "M";
  if (a >= 1e3) return (v / 1e3).toPrecision(3) + "k";
  if (a >= 1 || a === 0) return +v.toPrecision(4) + "";
  if (a >= 1e-3) return +(v * 1e3).toPrecision(3) + "m";
  return +(v * 1e6).toPrecision(3) + "µ";
}
function fmtTime(t, span) {
  if (span >= 2) return +t.toPrecision(4) + " s";
  if (span >= 2e-3) return +(t * 1e3).toPrecision(4) + " ms";
  return +(t * 1e6).toPrecision(4) + " µs";
}
