// Board viewer: draws WholeBoard geometry JSON on a canvas with pan, zoom and layer toggles.
"use strict";

const LAYER_COLORS = {
  "F.Cu": "#e5534b", "B.Cu": "#4d8df6", "In1.Cu": "#d6b33a", "In2.Cu": "#38c793",
  "In3.Cu": "#c45ad6", "In4.Cu": "#5ad6d0",
};

class BoardView {
  constructor(host, geo, opts = {}) {
    this.host = host;
    this.geo = geo;
    this.opts = opts;
    this.layers = new Set([...(geo.layers || []), "pads", "vias", "zones", "outline", "refs",
                           ...(opts.ratsnest ? ["ratsnest"] : []), ...(opts.courtyards ? ["courtyards"] : [])]);
    host.innerHTML = "";
    host.classList.add("boardview");
    this.canvas = document.createElement("canvas");
    this.tip = document.createElement("div");
    this.tip.className = "board-tip";
    this.bar = document.createElement("div");
    this.bar.className = "board-bar";
    host.append(this.canvas, this.tip, this.bar);
    this._buildBar();
    this.ctx = this.canvas.getContext("2d");
    this._bindEvents();
    this.ro = new ResizeObserver(() => { this._resize(); this.fit(); });
    this.ro.observe(host);
    this._onTheme = () => this.draw();
    window.addEventListener("wb-theme", this._onTheme);
    this._onZoom = () => { this._resize(); this.draw(); };
    window.addEventListener("wb-zoom", this._onZoom);
  }

  _buildBar() {
    const items = [...(this.geo.layers || []).map(l => [l, l]), ["zones", "Pour"], ["pads", "Pads"],
                   ["vias", "Vias"], ["ratsnest", "Air wires"], ["courtyards", "Courtyards"], ["refs", "Labels"]];
    for (const [key, label] of items) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.className = "chip toggle" + (this.layers.has(key) ? " on" : "");
      if (LAYER_COLORS[key]) b.style.setProperty("--dot", LAYER_COLORS[key]);
      b.onclick = () => {
        this.layers.has(key) ? this.layers.delete(key) : this.layers.add(key);
        b.classList.toggle("on");
        this.draw();
      };
      this.bar.append(b);
    }
    const fit = document.createElement("button");
    fit.type = "button"; fit.className = "chip"; fit.textContent = "Fit";
    fit.onclick = () => this.fit();
    this.bar.append(fit);
  }

  _resize() {
    // layout pixels; the backing store also covers the page zoom so it stays sharp
    const dpr = (window.devicePixelRatio || 1) * wbZoom();
    this.w = Math.max(100, this.host.clientWidth); this.h = Math.max(100, this.host.clientHeight);
    this.canvas.width = this.w * dpr; this.canvas.height = this.h * dpr;
    this.canvas.style.width = this.w + "px"; this.canvas.style.height = this.h + "px";
    this.dpr = dpr;
  }

  bounds() {
    if (this.geo.bbox) {
      // include parts placed beside the board (before placement)
      let [x0, y0, x1, y1] = this.geo.bbox;
      for (const p of this.geo.pads) { x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y); }
      return [x0, y0, x1, y1];
    }
    return [0, 0, 100, 100];
  }

  fit() {
    if (!this.w) this._resize();
    const [x0, y0, x1, y1] = this.bounds();
    const pad = 6;
    const s = Math.min((this.w - 40) / (x1 - x0 + pad * 2), (this.h - 80) / (y1 - y0 + pad * 2));
    this.scale = Math.max(0.5, s);
    this.ox = this.w / 2 - ((x0 + x1) / 2) * this.scale;
    this.oy = (this.h - 36) / 2 - ((y0 + y1) / 2) * this.scale;
    this.draw();
  }

  _bindEvents() {
    let drag = null;
    this.canvas.addEventListener("wheel", e => {
      e.preventDefault();
      const k = Math.exp(-e.deltaY * 0.0015);
      const r = this.canvas.getBoundingClientRect(), z = wbZoom();
      const mx = (e.clientX - r.left) / z, my = (e.clientY - r.top) / z;
      this.ox = mx - (mx - this.ox) * k; this.oy = my - (my - this.oy) * k;
      this.scale *= k;
      this.draw();
    }, { passive: false });
    this.canvas.addEventListener("pointerdown", e => { drag = [e.clientX, e.clientY]; this.canvas.setPointerCapture(e.pointerId); });
    this.canvas.addEventListener("pointerup", () => { drag = null; });
    this.canvas.addEventListener("pointermove", e => {
      if (drag) {
        const z = wbZoom();
        this.ox += (e.clientX - drag[0]) / z; this.oy += (e.clientY - drag[1]) / z;
        drag = [e.clientX, e.clientY];
        this.draw();
        return;
      }
      const r = this.canvas.getBoundingClientRect(), z = wbZoom();
      const mx = (e.clientX - r.left) / z, my = (e.clientY - r.top) / z;
      this._hover((mx - this.ox) / this.scale, (my - this.oy) / this.scale, mx, my);
    });
    this.canvas.addEventListener("pointerleave", () => { this.tip.style.display = "none"; });
  }

  _hover(x, y, sx, sy) {
    let best = null, bd = 1e9;
    for (const p of this.geo.pads) {
      const d = Math.hypot(p.x - x, p.y - y);
      if (d < Math.max(p.w, p.h) / 2 + 0.2 && d < bd) { bd = d; best = `${p.ref} pad ${p.name}` + (p.net ? ` · ${p.net}` : ""); }
    }
    if (!best) {
      for (const t of this.geo.tracks) {
        const d = segDist(x, y, t.a, t.b);
        if (d < t.w / 2 + 0.15 && d < bd) { bd = d; best = `${t.net || "track"} · ${t.layer} · ${t.w} mm`; }
      }
    }
    if (!best) {
      for (const f of this.geo.footprints) {
        if (f.court && x >= f.court[0] && x <= f.court[2] && y >= f.court[1] && y <= f.court[3]) {
          best = `${f.ref} · ${f.lib.split(":").pop()}`; break;
        }
      }
    }
    if (best) {
      this.tip.textContent = best;
      this.tip.style.display = "block";
      this.tip.style.left = (sx + 14) + "px"; this.tip.style.top = (sy + 10) + "px";
    } else this.tip.style.display = "none";
  }

  draw() {
    const c = this.ctx, g = this.geo;
    if (!c || !this.scale) return;
    const css = getComputedStyle(document.documentElement);
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    c.fillStyle = css.getPropertyValue("--board-bg").trim() || "#0f1512";
    c.fillRect(0, 0, this.w, this.h);
    c.setTransform(this.dpr * this.scale, 0, 0, this.dpr * this.scale, this.dpr * this.ox, this.dpr * this.oy);

    // substrate
    if (g.outline.length) {
      c.beginPath(); outlinePath(c, g.outline);
      c.fillStyle = css.getPropertyValue("--board-mask").trim() || "#1f4a35"; c.fill();
    }
    const cuOrder = [...(g.layers || [])].reverse(); // back first, front last
    for (const layer of cuOrder) {
      if (!this.layers.has(layer)) continue;
      const col = LAYER_COLORS[layer] || "#aaa";
      if (this.layers.has("zones")) {
        c.globalAlpha = layer === "F.Cu" ? 0.30 : 0.25;
        c.fillStyle = col;
        for (const z of g.zones) {
          if (z.layer !== layer || z.pts.length < 3) continue;
          c.beginPath(); c.moveTo(z.pts[0][0], z.pts[0][1]);
          for (const p of z.pts) c.lineTo(p[0], p[1]);
          c.closePath(); c.fill();
        }
        c.globalAlpha = 1;
      }
      c.strokeStyle = col; c.lineCap = "round"; c.lineJoin = "round";
      c.globalAlpha = layer === "F.Cu" ? 0.95 : 0.8;
      for (const t of g.tracks) {
        if (t.layer !== layer) continue;
        c.lineWidth = t.w; c.beginPath(); c.moveTo(t.a[0], t.a[1]); c.lineTo(t.b[0], t.b[1]); c.stroke();
      }
      for (const a of g.arcs) {
        if (a.layer !== layer) continue;
        c.lineWidth = a.w; c.beginPath(); arcPath(c, a.a, a.m, a.b); c.stroke();
      }
      c.globalAlpha = 1;
    }
    if (this.layers.has("pads")) {
      for (const p of g.pads) {
        const front = p.front && this.layers.has("F.Cu"), back = p.back && this.layers.has("B.Cu");
        if (!front && !back && p.kind !== "np_thru_hole") continue;
        c.save(); c.translate(p.x, p.y); c.rotate(-p.rot * Math.PI / 180);
        c.fillStyle = p.kind === "np_thru_hole" ? "#0b0f0d" : (p.kind === "thru_hole" ? "#e0b84f" : (front ? "#d4a43c" : "#7aa7e8"));
        padPath(c, p); c.fill();
        if (p.drill > 0) { c.fillStyle = "#0b0f0d"; c.beginPath(); c.arc(0, 0, p.drill / 2, 0, 7); c.fill(); }
        c.restore();
      }
    }
    if (this.layers.has("vias")) {
      for (const v of g.vias) {
        c.fillStyle = "#c9ced6"; c.beginPath(); c.arc(v.p[0], v.p[1], v.d / 2, 0, 7); c.fill();
        c.fillStyle = "#0b0f0d"; c.beginPath(); c.arc(v.p[0], v.p[1], v.drill / 2, 0, 7); c.fill();
      }
    }
    if (this.layers.has("outline") && g.outline.length) {
      c.strokeStyle = "#f3e7b3"; c.lineWidth = Math.max(0.15, 1.5 / this.scale);
      c.beginPath(); outlinePath(c, g.outline); c.stroke();
    }
    if (this.layers.has("courtyards")) {
      c.strokeStyle = "rgba(255,110,200,.7)"; c.lineWidth = 1 / this.scale; c.setLineDash([4 / this.scale, 3 / this.scale]);
      for (const f of g.footprints) if (f.court) c.strokeRect(f.court[0], f.court[1], f.court[2] - f.court[0], f.court[3] - f.court[1]);
      c.setLineDash([]);
    }
    if (this.layers.has("ratsnest") && g.ratsnest) {
      c.strokeStyle = "rgba(120,230,255,.75)"; c.lineWidth = 1 / this.scale;
      c.beginPath();
      for (const [a, b] of g.ratsnest) { c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); }
      c.stroke();
    }
    if (this.layers.has("refs") && this.scale > 3) {
      c.fillStyle = "rgba(255,255,255,.85)"; c.textAlign = "center"; c.textBaseline = "middle";
      const fs = Math.min(1.6, Math.max(0.7, 11 / this.scale));
      c.font = `600 ${fs}px Inter, system-ui, sans-serif`;
      for (const f of g.footprints) {
        if (!f.court) continue;
        c.fillText(f.ref, (f.court[0] + f.court[2]) / 2, (f.court[1] + f.court[3]) / 2);
      }
    }
  }
}

function segDist(x, y, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const l = dx * dx + dy * dy;
  let t = l ? ((x - a[0]) * dx + (y - a[1]) * dy) / l : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(x - (a[0] + t * dx), y - (a[1] + t * dy));
}

function circleFrom3(a, m, b) {
  const ax = a[0], ay = a[1], bx = m[0], by = m[1], cx = b[0], cy = b[1];
  const d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by));
  if (Math.abs(d) < 1e-9) return null;
  const ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d;
  const uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d;
  return [ux, uy, Math.hypot(ax - ux, ay - uy)];
}

function arcPath(c, a, m, b, move = true) {
  const k = circleFrom3(a, m, b);
  if (!k) { if (move) c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); return; }
  const [ux, uy, r] = k;
  const s = Math.atan2(a[1] - uy, a[0] - ux), e = Math.atan2(b[1] - uy, b[0] - ux), mid = Math.atan2(m[1] - uy, m[0] - ux);
  const norm = v => (v + 4 * Math.PI) % (2 * Math.PI);
  const ccw = !(norm(mid - s) < norm(e - s));
  if (move) c.moveTo(a[0], a[1]);
  c.arc(ux, uy, r, s, e, ccw);
}

function outlinePath(c, items) {
  // chain the outline pieces end to end
  const rest = items.slice();
  const near = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1]) < 0.01;
  while (rest.length) {
    let cur = rest.shift();
    if (cur.kind === "rect") { c.rect(cur.start[0], cur.start[1], cur.end[0] - cur.start[0], cur.end[1] - cur.start[1]); continue; }
    if (cur.kind === "circle") { c.moveTo(cur.end[0], cur.end[1]); c.arc(cur.center[0], cur.center[1], Math.hypot(cur.end[0] - cur.center[0], cur.end[1] - cur.center[1]), 0, 7); continue; }
    c.moveTo(cur.start[0], cur.start[1]);
    let end = cur.start;
    for (;;) {
      if (cur.kind === "arc") arcPath(c, cur.start, cur.mid, cur.end, false); else c.lineTo(cur.end[0], cur.end[1]);
      end = cur.end;
      const i = rest.findIndex(o => o.start && (near(o.start, end) || near(o.end, end)));
      if (i < 0) break;
      cur = rest.splice(i, 1)[0];
      if (!near(cur.start, end)) cur = { ...cur, start: cur.end, end: cur.start };
    }
    c.closePath();
  }
}

function padPath(c, p) {
  const w = p.w, h = p.h;
  c.beginPath();
  if (p.shape === "circle") c.arc(0, 0, w / 2, 0, 7);
  else if (p.shape === "oval") {
    const r = Math.min(w, h) / 2;
    roundRect(c, -w / 2, -h / 2, w, h, r);
  } else if (p.shape === "roundrect") roundRect(c, -w / 2, -h / 2, w, h, Math.min(w, h) * 0.25);
  else c.rect(-w / 2, -h / 2, w, h);
}

function roundRect(c, x, y, w, h, r) {
  c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
}

window.BoardView = BoardView;
