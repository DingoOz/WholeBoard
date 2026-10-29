// Placement live: moves the parts of the generated board as TraceMaker's placer streams its frames
// (tracemaker-place --live, read through the server's tail endpoint while the place step runs).
"use strict";

class PlaceLive {
  // v: the "live" visual (url: tail endpoint, base: the generated board's geometry); opts.running(): still placing?
  constructor(host, v, opts = {}) {
    this.host = host; this.v = v; this.opts = opts;
    this.offset = 0; this.queue = []; this.parts = null; this.done = false; this.stopped = false;
    this.phase = ""; this.frame = null; this.seen = false;
    host.classList.add("boardview");
    fetch(v.base).then(r => r.json()).then(geo => {
      if (this.stopped) return;
      this.geo = geo;
      this.view = new BoardView(host, geo, { compact: opts.compact, ratsnest: true });
      this.status = document.createElement("div");
      this.status.className = "place-live-status";
      this.status.textContent = "Waiting for the placer…";
      host.append(this.status);
      this._poll();
      this.timer = setInterval(() => this._play(), 100);
    });
  }

  destroy() {
    this.stopped = true;
    clearInterval(this.timer); clearTimeout(this.pollTimer);
    this.view?.destroy();
  }

  async _poll() {
    if (this.stopped) return;
    if (this.seen && !this.host.isConnected) { this.destroy(); return; }    // its card was replaced
    this.seen ||= this.host.isConnected;
    let more = false;
    try {
      const r = await fetch(`${this.v.url}?offset=${this.offset}`, { cache: "no-store" });
      if (r.status === 204) { if (this.parts) this.done = true; }   // no file: not started yet, or removed at the end
      else if (r.ok) {
        const next = Number(r.headers.get("X-Offset") || this.offset);
        const text = await r.text();
        if (next < this.offset) { this.parts = null; this.queue = []; }      // the file was started again
        const catchUp = this.offset === 0;
        this.offset = next;
        more = text.length >= (8 << 20) - 4096;                                // a full chunk: read on at once
        for (const line of text.split("\n")) if (line) this._take(JSON.parse(line));
        if (catchUp && this.queue.length > 1) this.queue = this.queue.slice(-1);  // joined late: show where it is
      }
    } catch (e) { /* the server restarted or the file is not there yet; try again */ }
    const running = this.opts.running ? this.opts.running() : true;
    if (!this.done && running) this.pollTimer = setTimeout(() => this._poll(), more ? 0 : 1000);
  }

  _take(m) {
    if (m.type === "place_header") this._header(m);
    else if (m.type === "place_frame" && this.parts) this.queue.push(m);
    else if (m.type === "place_phase") { this.phase = m.text; this._showStatus(); }
    else if (m.type === "place_done") this.done = true;
  }

  // Each part's pads and courtyard as offsets from its origin in the input placement.
  _header(h) {
    const byRef = {};
    for (const f of this.geo.footprints) byRef[f.ref] = { f, pads: [] };
    for (const p of this.geo.pads) byRef[p.ref]?.pads.push(p);
    this.parts = h.parts.map(pt => {
      const e = byRef[pt.ref];
      if (!e) return null;
      const x0 = pt.x0 / 1e6, y0 = pt.y0 / 1e6, f = e.f;
      return {
        f, x0, y0, side0: f.side,
        pads: e.pads.map(p => ({ p, dx: p.x - x0, dy: p.y - y0, rot: p.rot, front: p.front, back: p.back })),
        court: f.court && [[f.court[0], f.court[1]], [f.court[2], f.court[1]], [f.court[2], f.court[3]], [f.court[0], f.court[3]]]
          .map(([x, y]) => [x - x0, y - y0]),
      };
    });
  }

  _play() {
    if (!this.queue.length) return;
    if (this.queue.length > 30) this.queue = this.queue.slice(-30);   // annealing arrives in bursts: keep up
    this._apply(this.queue.shift());
  }

  // pos: [x nm, y nm, state]; state bits 0-1 = quarter turns from the input orientation, bit 2 = flipped to the
  // other side (KiCad's flip about the origin: offsets mirrored in y), as TraceMaker's Placement::rot.
  _apply(fr) {
    const n = Math.min(fr.pos.length, this.parts.length);
    for (let i = 0; i < n; i++) {
      const pt = this.parts[i];
      if (!pt) continue;
      const [xn, yn, s] = fr.pos[i], x = xn / 1e6, y = yn / 1e6, flip = (s & 4) !== 0, a = 90 * (s & 3);
      const cs = Math.cos(a * Math.PI / 180), sn = Math.sin(a * Math.PI / 180);
      const tr = (dx, dy) => { if (flip) dy = -dy; return [x + dx * cs + dy * sn, y - dx * sn + dy * cs]; };
      for (const q of pt.pads) {
        [q.p.x, q.p.y] = tr(q.dx, q.dy);
        q.p.rot = (flip ? -q.rot : q.rot) + a;
        q.p.front = flip ? q.back : q.front; q.p.back = flip ? q.front : q.back;
      }
      pt.f.x = x; pt.f.y = y;
      pt.f.side = flip ? (pt.side0 === "B.Cu" ? "F.Cu" : "B.Cu") : pt.side0;
      if (pt.court) {
        const c = pt.court.map(([dx, dy]) => tr(dx, dy));
        pt.f.court = [Math.min(...c.map(q => q[0])), Math.min(...c.map(q => q[1])), Math.max(...c.map(q => q[0])), Math.max(...c.map(q => q[1]))];
      }
    }
    this.geo.ratsnest = airWires(this.geo.pads);
    this.frame = fr;
    this._showStatus();
    if (!this.fitted && fr.stage !== "input" && this.geo.bbox) {
      // the parts have left the pile beside the board: frame the board once, then leave pan and zoom to the viewer
      this.fitted = true;
      this.view.bounds = () => this.geo.bbox;
      this.view.fit();
    } else this.view.draw();
  }

  _showStatus() {
    if (!this.status) return;
    const fr = this.frame;
    const what = fr ? [PLACE_STAGES[fr.stage] || fr.stage, fr.label !== fr.stage ? fr.label : "",
                       fr.hpwl ? `wire length ${Math.round(fr.hpwl / 1e6).toLocaleString()} mm` : ""].filter(Boolean).join(" · ") : "";
    this.status.textContent = [what, this.phase].filter(Boolean).join("\n") || "Waiting for the placer…";
  }
}

const PLACE_STAGES = {
  input: "start", quadratic: "spreading out (quadratic)", spreading: "spreading out", rotations: "turning parts",
  annealing: "annealing", lns: "polishing", placed: "placed", eco: "moving a part for the router", result: "result",
};

// Air wires: a minimum spanning tree per net over its pads, as the server's geometry.ratsnest.
function airWires(pads) {
  const byNet = new Map();
  for (const p of pads) if (p.net) { let a = byNet.get(p.net); if (!a) byNet.set(p.net, a = []); a.push(p); }
  const lines = [];
  for (const pts of byNet.values()) {
    const n = pts.length;
    if (n < 2 || n > 400) continue;
    const best = new Float64Array(n).fill(Infinity), from = new Int32Array(n), done = new Uint8Array(n);
    let cur = 0; done[0] = 1;
    for (let k = 1; k < n; k++) {
      let pick = -1;
      for (let i = 0; i < n; i++) {
        if (done[i]) continue;
        const d = Math.hypot(pts[i].x - pts[cur].x, pts[i].y - pts[cur].y);
        if (d < best[i]) { best[i] = d; from[i] = cur; }
        if (pick < 0 || best[i] < best[pick]) pick = i;
      }
      done[pick] = 1; cur = pick;
      lines.push([[pts[from[pick]].x, pts[from[pick]].y], [pts[pick].x, pts[pick].y]]);
    }
  }
  return lines;
}

window.PlaceLive = PlaceLive;
