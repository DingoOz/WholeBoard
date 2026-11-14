// Net tracing in the schematic: pick a net, label, power net, part or bus member and follow it round every sheet.
//
// The schematic SVG is drawn as it is; an SVG overlay with the same viewBox sits on it and moves with it, so the
// highlights follow pan and zoom. The positions come from the sidecar the build writes beside the SVGs
// (wholeboard/kicad/schnets.py), in schematic millimetres, which are also the SVG's own units.
"use strict";

const TRACE_STATE = {};          // nets URL -> what the view showed, so a re-rendered card picks up where it was
const NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
  return e;
}

function tracedKey(pid) { return "wb-traced-" + (pid || "none"); }
function loadTraced(pid) {
  try { return new Set(JSON.parse(localStorage.getItem(tracedKey(pid)) || "[]")); } catch (e) { return new Set(); }
}
function saveTraced(pid, set) {
  try { localStorage.setItem(tracedKey(pid), JSON.stringify([...set])); } catch (e) { /* private window: kept for this page only */ }
}

function segDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay, l = dx * dx + dy * dy;
  const t = l ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / l)) : 0;
  return Math.hypot(px - ax - t * dx, py - ay - t * dy);
}
const inBox = (b, x, y, pad = 0) => x >= b[0] - pad && x <= b[2] + pad && y >= b[1] - pad && y <= b[3] + pad;
const isWire = k => k === "wire" || k === "bus";

class SchematicTracer {
  // v: {title, url, pages?: [{name, title, url, parts}], nets: sidecar URL}; opts: {pid, el}
  constructor(host, v, opts = {}) {
    this.v = v; this.opts = opts; this.el = opts.el;
    this.pages = v.pages?.length ? v.pages : [{ name: "", title: "Main", url: v.url }];
    this.key = (v.nets || v.url || "").split("?")[0];
    const st = TRACE_STATE[this.key] || (TRACE_STATE[this.key] = { page: 0, trace: null, seen: new Set(), cur: -1, view: {} });
    this.st = st;
    this.traced = loadTraced(opts.pid);
    this.s = 1; this.x = 0; this.y = 0;
    this.build(host);
    this.showPage(st.page, true);
    if (v.nets) fetch(v.nets).then(r => r.json()).then(d => this.setData(d), e => {
      console.error(e); this.status("Net positions could not be loaded, so nets cannot be traced here.");
    });
    SchematicTracer.current = this;
  }

  // ------------------------------------------------------------ layout
  build(host) {
    const E = this.el;
    this.wrap = E("div", { class: "sheet-wrap trace-wrap", tabindex: "-1" });
    this.tabs = E("div", { class: "sheet-tabs", role: "tablist" });
    this.tabBtns = this.pages.map((pg, i) => {
      const b = E("button", { type: "button", role: "tab", title: `${pg.parts ?? "?"} parts` }, E("span", {}, pg.title), E("span", { class: "trace-count" }));
      b.onclick = () => this.showPage(i);
      this.tabs.append(b);
      return b;
    });
    if (this.pages.length < 2) this.tabs.hidden = true;

    this.input = E("input", { type: "search", class: "trace-search", placeholder: "Trace a net or part…", "aria-label": "Net or part to trace", list: "traceList" + Math.random().toString(36).slice(2, 8) });
    this.datalist = E("datalist", { id: this.input.getAttribute("list") });
    this.input.addEventListener("change", () => this.pick(this.input.value.trim()));
    this.input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); this.pick(this.input.value.trim()); } });
    this.counter = E("span", { class: "trace-counter", "aria-live": "polite" });
    const btn = (label, title, fn) => E("button", { type: "button", class: "trace-btn", title, onclick: fn }, label);
    this.prevBtn = btn("‹", "Previous place (P)", () => this.step(-1));
    this.nextBtn = btn("›", "Next place (N)", () => this.step(1));
    this.clearBtn = btn("✕", "Stop tracing (Esc)", () => this.clear());
    this.listBtn = btn("Checklist", "Nets traced so far: a review checklist", () => this.toggleList());
    this.bar = E("div", { class: "trace-bar" }, this.input, this.datalist, this.prevBtn, this.counter, this.nextBtn, this.clearBtn, E("span", { class: "spacer" }), this.listBtn);

    this.box = E("div", { class: "zoomimg trace-view", tabindex: "0", "aria-label": "Schematic. Click a wire, label, pin or part to trace it; N and P move between its places." });
    this.stage = E("div", { class: "trace-stage" });
    this.img = E("img", { alt: this.v.title || "Schematic", draggable: "false" });
    this.overlay = svgEl("svg", { class: "trace-overlay" });
    this.stage.append(this.img, this.overlay);
    this.arrows = E("div", { class: "trace-arrows" });
    this.mini = E("div", { class: "trace-mini", title: "Drag to move the view" });
    this.miniImg = E("img", { alt: "", draggable: "false" });
    this.miniSvg = svgEl("svg", { class: "trace-mini-svg" });
    this.mini.append(this.miniImg, this.miniSvg);
    this.panel = E("div", { class: "trace-panel", hidden: true });
    this.box.append(this.stage, this.arrows, this.mini, this.panel);
    this.wrap.append(this.tabs, this.bar, this.box);
    host.append(this.wrap);

    this.img.addEventListener("load", () => this.fit());
    this.panZoom();
    this.miniDrag();
    this.wrap.addEventListener("keydown", e => {
      if (e.target === this.input) return;
      if (e.key === "n" || e.key === "N") { e.preventDefault(); this.step(1); }
      else if (e.key === "p" || e.key === "P") { e.preventDefault(); this.step(-1); }
      else if (e.key === "Escape" && this.st.trace) { e.preventDefault(); this.clear(); }
    });
    this.resize = new ResizeObserver(() => this.apply());
    this.resize.observe(this.box);
  }

  status(text) { this.counter.textContent = text; }

  // ------------------------------------------------------------ data
  setData(d) {
    this.data = d;
    this.sheetOf = this.pages.map(pg => d.sheets.findIndex(sh => sh.name === (pg.name || "")));
    if (this.pages.length === 1 && this.sheetOf[0] < 0) this.sheetOf[0] = 0;
    this.pageOf = {};
    this.sheetOf.forEach((s, i) => { if (s >= 0) this.pageOf[s] = i; });
    this.datalist.replaceChildren(...Object.keys(d.nets).map(n => this.el("option", { value: n })),
      ...Object.entries(d.parts).map(([r, p]) => this.el("option", { value: r }, p.value || "")));
    this.netOrder = Object.keys(d.nets).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    // the picture may have loaded first, measured against a guessed viewBox: measure again, keeping the view
    if (this.img.complete && this.img.naturalWidth) { this.restoreView = { s: this.s, x: this.x, y: this.y }; this.fit(); }
    if (SchematicTracer.pending) {
      const r = SchematicTracer.pending; SchematicTracer.pending = null;
      this.wrap.scrollIntoView({ block: "nearest" });
      this.trace(r);
    }
    else if (this.st.trace) this.trace(this.st.trace, { keep: true });
    else { this.status(`${this.netOrder.length} nets. Click one to trace it.`); this.draw(); }
  }

  vb(page = this.st.page) {
    const s = this.sheetOf?.[page];
    return this.data?.sheets[s]?.viewBox || [0, 0, 297, 210];
  }

  // ------------------------------------------------------------ pages and view
  showPage(i, first) {
    i = Math.max(0, Math.min(this.pages.length - 1, i | 0));
    if (!first && i === this.st.page && this.img.src) return;
    if (!first) this.st.view[this.st.page] = { s: this.s, x: this.x, y: this.y };
    this.st.page = i;
    const pg = this.pages[i];
    this.tabBtns.forEach((b, j) => b.setAttribute("aria-selected", j === i ? "true" : "false"));
    this.restoreView = this.st.view[i];
    this.img.src = pg.url; this.miniImg.src = pg.url;
    this.draw();
  }

  fit() {
    const vb = this.vb();
    this.w = this.img.naturalWidth || vb[2] * 3.7795;
    this.h = this.img.naturalHeight || vb[3] * 3.7795;
    this.k = this.w / vb[2];
    this.overlay.setAttribute("width", this.w); this.overlay.setAttribute("height", this.h);
    this.overlay.setAttribute("viewBox", vb.join(" "));
    this.miniSvg.setAttribute("viewBox", vb.join(" "));
    const mw = 168, mh = Math.max(40, Math.min(150, mw * vb[3] / vb[2]));
    this.mini.style.width = mw + "px"; this.mini.style.height = mh + "px";
    const r = this.restoreView;
    if (r) { this.s = r.s; this.x = r.x; this.y = r.y; this.restoreView = null; }
    else {
      const bw = this.box.clientWidth, bh = this.box.clientHeight;
      this.s = Math.min(bw / this.w, bh / this.h) * 0.95 || 1;
      this.x = (bw - this.w * this.s) / 2; this.y = (bh - this.h * this.s) / 2;
    }
    this.apply();
    this.draw();
    if (this.goAfterLoad != null) { const g = this.goAfterLoad; this.goAfterLoad = null; this.focusOcc(g); }
  }

  apply() {
    this.stage.style.transform = `translate(${this.x}px, ${this.y}px) scale(${this.s})`;
    this.drawView();
    this.markSeen();
    this.drawArrows();
  }

  zoomScale() { return typeof wbZoom === "function" ? wbZoom() : 1; }

  // sheet mm -> box pixels, and back
  toBox(sx, sy) { const vb = this.vb(); return [this.x + (sx - vb[0]) * this.k * this.s, this.y + (sy - vb[1]) * this.k * this.s]; }
  toSheet(bx, by) { const vb = this.vb(); return [vb[0] + (bx - this.x) / (this.k * this.s), vb[1] + (by - this.y) / (this.k * this.s)]; }

  panZoom() {
    const box = this.box;
    let drag = null, moved = 0;
    box.addEventListener("wheel", e => {
      e.preventDefault();
      const r = box.getBoundingClientRect(), z = this.zoomScale(), mx = (e.clientX - r.left) / z, my = (e.clientY - r.top) / z;
      const k = Math.exp(-e.deltaY * 0.0015);
      this.x = mx - (mx - this.x) * k; this.y = my - (my - this.y) * k; this.s *= k; this.apply();
    }, { passive: false });
    box.addEventListener("pointerdown", e => {
      if (e.target.closest(".trace-mini, .trace-arrow, .trace-panel")) return;
      drag = [e.clientX, e.clientY]; moved = 0; box.setPointerCapture(e.pointerId);
    });
    box.addEventListener("pointermove", e => {
      if (!drag) return;
      const z = this.zoomScale();
      moved += Math.abs(e.clientX - drag[0]) + Math.abs(e.clientY - drag[1]);
      this.x += (e.clientX - drag[0]) / z; this.y += (e.clientY - drag[1]) / z; drag = [e.clientX, e.clientY]; this.apply();
    });
    box.addEventListener("pointerup", e => {
      if (!drag) return;
      drag = null;
      if (moved < 5) {
        const r = box.getBoundingClientRect(), z = this.zoomScale();
        this.click(...this.toSheet((e.clientX - r.left) / z, (e.clientY - r.top) / z));
      }
    });
  }

  miniDrag() {
    let on = false;
    const go = e => {
      const r = this.mini.getBoundingClientRect(), vb = this.vb();
      const sx = vb[0] + (e.clientX - r.left) / r.width * vb[2], sy = vb[1] + (e.clientY - r.top) / r.height * vb[3];
      this.centre(sx, sy);
    };
    this.mini.addEventListener("pointerdown", e => { on = true; this.mini.setPointerCapture(e.pointerId); go(e); e.stopPropagation(); });
    this.mini.addEventListener("pointermove", e => { if (on) go(e); });
    this.mini.addEventListener("pointerup", () => { on = false; });
  }

  centre(sx, sy, scale) {
    if (scale) this.s = scale;
    const bw = this.box.clientWidth, bh = this.box.clientHeight;
    const vb = this.vb();
    this.x = bw / 2 - (sx - vb[0]) * this.k * this.s;
    this.y = bh / 2 - (sy - vb[1]) * this.k * this.s;
    this.apply();
  }

  // ------------------------------------------------------------ picking
  click(sx, sy) {
    if (!this.data) return;
    const s = this.sheetOf[this.st.page];
    const hits = [];
    for (const [name, n] of Object.entries(this.data.nets)) {
      for (const i of n.items) {
        if (i.s !== s) continue;
        let d = Infinity;
        if (i.box && inBox(i.box, sx, sy, 0.3)) d = 0.1;
        else if (i.k === "pin") d = segDist(sx, sy, i.x, i.y, i.x2, i.y2);
        else if (isWire(i.k)) d = segDist(sx, sy, i.x, i.y, i.x2, i.y2);
        else d = Math.hypot(sx - i.x, sy - i.y);
        if (d < (i.k === "bus" ? 0.9 : 0.7)) hits.push({ d, req: { net: name } });
      }
    }
    if (hits.length) {
      hits.sort((a, b) => a.d - b.d);
      const nets = [...new Set(hits.filter(h => h.d - hits[0].d < 0.3).map(h => h.req.net))];
      // a bus or a bus label carries several nets: clicking it again moves to the next member
      const at = nets.indexOf(this.st.trace?.net);
      this.note = nets.length > 1 ? `bus: ${nets.length} members here, click again for the next` : "";
      this.trace({ net: nets[at >= 0 ? (at + 1) % nets.length : 0] });
      return;
    }
    const block = (this.data.blocks || []).find(b => b.s === s && inBox(b.box, sx, sy));
    const part = Object.entries(this.data.parts).find(([, p]) => p.items.some(i => i.s === s && inBox(i.box, sx, sy, 0.3)));
    if (part) return this.trace({ part: part[0] });
    if (block) { const i = this.pages.findIndex(pg => pg.name === block.sheet); if (i >= 0) this.showPage(i); }
  }

  pick(text) {
    if (!text || !this.data) return;
    this.note = "";
    if (this.data.nets[text]) return this.trace({ net: text });
    if (this.data.parts[text]) return this.trace({ part: text });
    const low = text.toLowerCase();
    const net = Object.keys(this.data.nets).find(n => n.toLowerCase() === low) || Object.keys(this.data.nets).find(n => n.toLowerCase().includes(low));
    if (net) return this.trace({ net });
    const part = Object.keys(this.data.parts).find(r => r.toLowerCase() === low);
    if (part) return this.trace({ part });
    this.status(`No net or part called ${text} on this schematic.`);
  }

  // The places a net or part appears, in sheet order then top to bottom. Items of a net that sit together
  // (a pin and the label on its end) count as one place.
  occurrences(req) {
    const out = [];
    const order = s => this.pageOf[s] ?? 999;
    if (req.part) {
      for (const i of this.data.parts[req.part]?.items || []) {
        const b = i.box;
        out.push({ s: i.s, x: (b[0] + b[2]) / 2, y: (b[1] + b[3]) / 2, box: b, unit: i.unit });
      }
    } else {
      for (const i of this.data.nets[req.net]?.items || []) {
        if (isWire(i.k)) continue;
        const near = out.find(o => o.s === i.s && Math.hypot(o.x - i.x, o.y - i.y) < 1.5);
        if (near) { near.kinds.add(i.k); continue; }
        out.push({ s: i.s, x: i.x, y: i.y, kinds: new Set([i.k]) });
      }
    }
    return out.sort((a, b) => order(a.s) - order(b.s) || a.y - b.y || a.x - b.x);
  }

  trace(req, { keep = false } = {}) {
    if (!this.data) { SchematicTracer.pending = req; return; }
    if (req.net && !this.data.nets[req.net]) {
      const alt = Object.keys(this.data.nets).find(n => n.toLowerCase() === req.net.toLowerCase());
      if (!alt) { this.status(`${req.net} is not on this schematic.`); return; }
      req = { net: alt };
    }
    if (req.part && !this.data.parts[req.part]) { this.status(`${req.part} is not on this schematic.`); return; }
    const same = this.st.trace && this.st.trace.net === req.net && this.st.trace.part === req.part;
    this.st.trace = req;
    this.occ = this.occurrences(req);
    if (!keep && !same) { this.st.seen = new Set(); this.st.cur = -1; }
    this.input.value = req.net || req.part;
    const counts = {};
    for (const o of this.occ) counts[o.s] = (counts[o.s] || 0) + 1;
    this.tabBtns.forEach((b, i) => {
      const n = counts[this.sheetOf[i]] || 0;
      b.querySelector(".trace-count").textContent = n ? String(n) : "";
      b.classList.toggle("has-trace", n > 0);
    });
    if (!keep && !same && this.occ.length) this.go(0);
    else { this.draw(); this.markSeen(); }
  }

  clear() {
    this.st.trace = null; this.occ = []; this.st.seen = new Set(); this.st.cur = -1;
    this.input.value = "";
    this.tabBtns.forEach(b => { b.querySelector(".trace-count").textContent = ""; b.classList.remove("has-trace"); });
    this.status(this.data ? `${this.netOrder.length} nets. Click one to trace it.` : "");
    this.draw();
  }

  step(d) {
    if (!this.occ?.length) return;
    const n = this.occ.length;
    this.go(((this.st.cur < 0 ? (d > 0 ? -1 : 0) : this.st.cur) + d + n) % n);
  }

  go(i) {
    this.st.cur = i;
    const o = this.occ[i];
    const page = this.pageOf[o.s];
    if (page != null && page !== this.st.page) {
      this.goAfterLoad = i;
      this.showPage(page);
      return;
    }
    this.focusOcc(i);
  }

  focusOcc(i) {
    const o = this.occ[i];
    if (!o) return;
    if (!this.k) { this.goAfterLoad = i; return; }
    // close enough to read the labels (1.27 mm text at about 11 px), or nearer if the view already is
    const readable = 9 / this.k;
    this.st.seen.add(i);
    this.centre(o.x, o.y, Math.max(this.s, readable));
    this.draw();
  }

  // an occurrence counts as seen once it has been on screen at a size where it can be read
  markSeen() {
    if (!this.occ?.length || !this.k) return;
    const s = this.sheetOf?.[this.st.page];
    if (this.k * this.s < 2.5) { this.updateCounter(); return; }
    const bw = this.box.clientWidth, bh = this.box.clientHeight;
    let added = false;
    this.occ.forEach((o, i) => {
      if (o.s !== s || this.st.seen.has(i)) return;
      const [x, y] = this.toBox(o.x, o.y);
      if (x >= 0 && y >= 0 && x <= bw && y <= bh) { this.st.seen.add(i); added = true; }
    });
    if (added) this.drawMiniDots();
    this.updateCounter();
  }

  updateCounter() {
    const req = this.st.trace;
    this.listBtn.textContent = `Checklist ${[...this.traced].filter(t => this.data?.nets[t]).length}/${this.netOrder?.length || 0}`;
    if (!req || !this.occ) return;       // (a re-rendered card works out the places once its data is in)
    const n = this.occ.length, seen = this.st.seen.size;
    const cur = this.occ[this.st.cur];
    const sheetTitle = s => this.pages[this.pageOf[s]]?.title || "another";
    let text = n ? (cur ? `${this.st.cur + 1} of ${n}` : `${n} place${n === 1 ? "" : "s"}`) : "Not drawn on any sheet";
    const sheetN = cur ? this.occ.filter(o => o.s === cur.s).length : 0;
    if (cur && this.pages.length > 1) text += `, ${sheetN} on the ${sheetTitle(cur.s)} sheet`;
    const done = n > 0 && seen >= n;
    if (n) text += done ? " · traced ✓" : ` · ${seen} of ${n} seen`;
    if (this.note && req.net) text += " · " + this.note;
    this.status(text);
    if (done && req.net && !this.traced.has(req.net)) {
      this.traced.add(req.net); saveTraced(this.opts.pid, this.traced);
      this.listBtn.textContent = `Checklist ${[...this.traced].filter(t => this.data?.nets[t]).length}/${this.netOrder?.length || 0}`;
      if (!this.panel.hidden) this.renderList();
    }
    this.wrap.classList.toggle("is-traced", done);
  }

  // ------------------------------------------------------------ drawing
  draw() {
    const o = this.overlay;
    o.replaceChildren();
    if (!this.data || !this.k) { this.drawMiniDots(); this.drawArrows(); return; }
    const vb = this.vb(), s = this.sheetOf[this.st.page], req = this.st.trace;
    if (req) {
      o.append(svgEl("rect", { x: vb[0], y: vb[1], width: vb[2], height: vb[3], class: "trace-dim" }));
      const g = svgEl("g", { class: "trace-hi" });
      if (req.net) {
        for (const i of this.data.nets[req.net]?.items || []) {
          if (i.s !== s) continue;
          if (isWire(i.k)) g.append(svgEl("line", { x1: i.x, y1: i.y, x2: i.x2, y2: i.y2, class: i.k === "bus" ? "t-bus" : "t-wire" }));
          else if (i.k === "pin") {
            g.append(svgEl("line", { x1: i.x, y1: i.y, x2: i.x2, y2: i.y2, class: "t-pin" }));
            g.append(svgEl("circle", { cx: i.x, cy: i.y, r: 0.55, class: "t-dot" }));
          } else if (i.box) g.append(svgEl("rect", { x: i.box[0], y: i.box[1], width: i.box[2] - i.box[0], height: i.box[3] - i.box[1], rx: 0.4, class: "t-label" }));
          else g.append(svgEl("circle", { cx: i.x, cy: i.y, r: 0.9, class: "t-dot" }));
        }
      } else {
        for (const i of this.data.parts[req.part]?.items || []) {
          if (i.s !== s) continue;
          const b = i.box;
          g.append(svgEl("rect", { x: b[0] - 1, y: b[1] - 1, width: b[2] - b[0] + 2, height: b[3] - b[1] + 2, rx: 0.8, class: "t-part" }));
        }
        for (const n of Object.values(this.data.nets))
          for (const i of n.items) if (i.s === s && i.k === "pin" && i.ref === req.part)
            g.append(svgEl("line", { x1: i.x, y1: i.y, x2: i.x2, y2: i.y2, class: "t-pin" }));
      }
      const cur = this.occ?.[this.st.cur];
      if (cur && cur.s === s) g.append(svgEl("circle", { cx: cur.x, cy: cur.y, r: 3, class: "t-current" }));
      o.append(g);
    }
    this.drawMiniDots();
    this.drawArrows();
    this.updateCounter();
  }

  drawMiniDots() {
    const m = this.miniSvg;
    m.replaceChildren();
    const vb = this.vb();
    this.viewRect = svgEl("rect", { class: "trace-mini-view" });
    if (this.occ?.length && this.data) {
      const s = this.sheetOf[this.st.page], r = vb[2] / 168 * 3.2;
      this.occ.forEach((o, i) => {
        if (o.s !== s) return;
        m.append(svgEl("circle", { cx: o.x, cy: o.y, r, class: "trace-mini-dot" + (this.st.seen.has(i) ? " seen" : "") + (i === this.st.cur ? " cur" : "") }));
      });
    }
    m.append(this.viewRect);
    this.drawView();
  }

  drawView() {
    if (!this.viewRect || !this.k) return;
    const [x0, y0] = this.toSheet(0, 0), [x1, y1] = this.toSheet(this.box.clientWidth, this.box.clientHeight);
    const vb = this.vb();
    const sw = vb[2] / 168 * 1.5;
    Object.entries({ x: x0, y: y0, width: Math.max(0, x1 - x0), height: Math.max(0, y1 - y0), "stroke-width": sw })
      .forEach(([k, v]) => this.viewRect.setAttribute(k, v));
  }

  drawArrows() {
    this.arrows.replaceChildren();
    if (!this.occ?.length || !this.data || !this.k) return;
    const s = this.sheetOf[this.st.page], bw = this.box.clientWidth, bh = this.box.clientHeight;
    const cx = bw / 2, cy = bh / 2, m = 18;
    const off = [];
    this.occ.forEach((o, i) => {
      if (o.s !== s) return;
      const [x, y] = this.toBox(o.x, o.y);
      if (x >= 0 && y >= 0 && x <= bw && y <= bh) return;
      off.push({ i, x, y, d: Math.hypot(x - cx, y - cy) });
    });
    off.sort((a, b) => a.d - b.d);
    const placed = [];
    for (const a of off.slice(0, 16)) {
      const dx = a.x - cx, dy = a.y - cy;
      const t = Math.min((bw / 2 - m) / Math.abs(dx || 1e-9), (bh / 2 - m) / Math.abs(dy || 1e-9));
      const ax = cx + dx * t, ay = cy + dy * t;
      if (placed.some(([px, py]) => Math.hypot(px - ax, py - ay) < 22)) continue;
      placed.push([ax, ay]);
      const ang = Math.atan2(dy, dx) * 180 / Math.PI;
      const b = this.el("button", { type: "button", class: "trace-arrow" + (this.st.seen.has(a.i) ? " seen" : ""),
        title: `Place ${a.i + 1} of ${this.occ.length}`, "aria-label": `Go to place ${a.i + 1} of ${this.occ.length}`,
        style: `left:${ax}px;top:${ay}px;transform:translate(-50%,-50%) rotate(${ang}deg)` }, "➤");
      b.addEventListener("pointerdown", e => e.stopPropagation());
      b.onclick = e => { e.stopPropagation(); this.go(a.i); };
      this.arrows.append(b);
    }
  }

  // ------------------------------------------------------------ checklist
  toggleList() {
    this.panel.hidden = !this.panel.hidden;
    if (!this.panel.hidden) this.renderList();
  }

  renderList() {
    const E = this.el;
    if (!this.data) return;
    const nets = this.netOrder;
    const done = nets.filter(n => this.traced.has(n)).length;
    const rows = nets.map(n => {
      const box = E("input", { type: "checkbox", "aria-label": `${n} traced` });
      box.checked = this.traced.has(n);
      box.onchange = () => { box.checked ? this.traced.add(n) : this.traced.delete(n); saveTraced(this.opts.pid, this.traced); this.renderList(); this.updateCounter(); };
      const name = E("button", { type: "button", class: "trace-link" + (this.data.nets[n].power ? " power" : "") }, n);
      name.onclick = () => this.trace({ net: n });
      return E("li", { class: this.st.trace?.net === n ? "on" : "" }, box, name);
    });
    this.panel.replaceChildren(
      E("div", { class: "trace-panel-head" }, E("b", {}, `Traced ${done} of ${nets.length} nets`),
        E("button", { type: "button", class: "trace-btn", title: "Close", onclick: () => this.toggleList() }, "✕")),
      E("p", { class: "muted" }, "A net is ticked once you have seen every place it appears. Untick to review it again."),
      E("ul", {}, rows));
  }
}
SchematicTracer.current = null;
SchematicTracer.pending = null;
