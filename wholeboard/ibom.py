"""An interactive assembly page: one self-contained HTML file with the board drawing and a clickable BOM."""

from __future__ import annotations

import html
import json
from pathlib import Path

from . import design
from .kicad import libs


def _groups(spec: dict, geo: dict) -> list[dict]:
    side = {f["ref"]: f["side"] for f in geo["footprints"]}
    groups: dict[tuple, dict] = {}
    for c in spec["components"]:
        fid = c.get("footprint") or ""
        if fid.startswith(("MountingHole", "TestPoint")):
            continue
        fp = libs.load_footprint(fid)
        tht = bool(fp and any(p.kind == "thru_hole" for p in fp.pads))
        jlc = bool(c.get("lcsc")) and not c.get("dnp")
        key = (c.get("value") or "", fid, c.get("lcsc") or "")
        g = groups.setdefault(key, {"value": c.get("value") or "", "footprint": fid.split(":")[-1],
                                    "lcsc": c.get("lcsc") or "", "description": c.get("description") or "",
                                    "refs": [], "tht": tht,
                                    "fit": "JLCPCB" if jlc else ("not fitted" if c.get("dnp") else "by hand")})
        g["refs"].append(c["ref"])
        if side.get(c["ref"]) == "B.Cu":
            g["back"] = True
    out = list(groups.values())
    for g in out:
        g["refs"].sort(key=lambda r: (r.rstrip("0123456789"), int("0" + "".join(ch for ch in r if ch.isdigit()))))
    out.sort(key=lambda g: (g["fit"] == "JLCPCB", g["refs"][0].rstrip("0123456789"), g["refs"][0]))
    return out


def generate(spec: dict, geo: dict, out: Path, title: str = "") -> Path:
    spec = design.normalise(spec)
    data = {
        "title": title or spec.get("name"),
        "outline": geo["outline"], "bbox": geo.get("bbox"),
        "footprints": [{k: f.get(k) for k in ("ref", "x", "y", "rot", "side", "court")} for f in geo["footprints"]],
        "pads": [{k: p[k] for k in ("ref", "x", "y", "w", "h", "rot", "shape", "kind", "drill", "front", "back")}
                 for p in geo["pads"]],
        "bom": _groups(spec, geo),
    }
    page = TEMPLATE.replace("__TITLE__", html.escape(str(data["title"]))).replace(
        "__DATA__", json.dumps(data, separators=(",", ":")).replace("</", "<\\/"))
    out.write_text(page, encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ assembly</title>
<style>
:root { --bg:#f6f7f9; --panel:#fff; --ink:#1d2330; --muted:#677084; --line:#d9dde5; --board:#2f6b3a; --edge:#d4b26a;
  --pad:#c9a74a; --court:#ffffff55; --hi:#ff3d7f; --done:#9aa3b5; }
@media (prefers-color-scheme: dark) { :root { --bg:#14171d; --panel:#1c2028; --ink:#e6e9ef; --muted:#9aa3b5; --line:#2c323d;
  --board:#1f4a28; --edge:#c9a85c; --pad:#b8973c; --court:#ffffff33; } }
* { box-sizing:border-box; }
body { margin:0; font:14px/1.4 system-ui, sans-serif; background:var(--bg); color:var(--ink); }
header { display:flex; gap:12px; align-items:center; padding:10px 16px; border-bottom:1px solid var(--line); flex-wrap:wrap; }
header h1 { font-size:16px; margin:0 auto 0 0; }
button, input { font:inherit; color:inherit; background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:5px 10px; }
button.on { border-color:var(--hi); }
main { display:grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 42%); height:calc(100vh - 52px); }
@media (max-width: 800px) { main { grid-template-columns: 1fr; height:auto; } #view { height:60vh; } }
#view { position:relative; overflow:hidden; background:var(--bg); }
svg { width:100%; height:100%; display:block; }
#list { overflow:auto; border-left:1px solid var(--line); background:var(--panel); }
table { border-collapse:collapse; width:100%; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { position:sticky; top:0; background:var(--panel); font-weight:600; }
tr.row { cursor:pointer; }
tr.row:hover, tr.row.sel { background:color-mix(in srgb, var(--hi) 14%, transparent); }
tr.row.placed td:not(:first-child) { color:var(--done); text-decoration:line-through; }
.tag { font-size:11px; padding:1px 6px; border-radius:9px; border:1px solid var(--line); white-space:nowrap; }
.tag.hand { border-color:var(--hi); color:var(--hi); }
.muted { color:var(--muted); }
.court { fill:none; stroke:var(--court); stroke-width:0.08; }
.court.hl { fill:color-mix(in srgb, var(--hi) 35%, transparent); stroke:var(--hi); stroke-width:0.2; }
.pad { fill:var(--pad); } .pad.hl { fill:var(--hi); }
.hole { fill:var(--bg); }
.edge { fill:none; stroke:var(--edge); stroke-width:0.25; }
.ref { fill:var(--ink); opacity:.75; text-anchor:middle; dominant-baseline:middle; pointer-events:none; }
.fp { cursor:pointer; }
</style></head><body>
<header><h1>__TITLE__ — assembly</h1>
<button id="front" class="on">Top</button><button id="back">Bottom</button>
<button id="hand">Hand-solder only</button>
<input id="filter" placeholder="Filter: R5, 10k, LED…" size="18">
<span class="muted" id="count"></span></header>
<main><div id="view"><svg id="svg"></svg></div><div id="list"><table><thead><tr><th>✓</th><th>Refs</th><th>Value</th><th>Footprint</th><th>Fitted</th></tr></thead><tbody id="rows"></tbody></table></div></main>
<script>
const D = __DATA__;
const NS = "http://www.w3.org/2000/svg";
const KEY = "ibom:" + D.title;
let placed = {}; try { placed = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(placed)); } catch (e) {} };
let side = "F.Cu", handOnly = false, sel = null;
const mk = (tag, attrs) => { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); return n; };

function arcPath(s, m, e) {
  const [ax, ay] = s, [bx, by] = m, [cx, cy] = e;
  const d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by));
  if (Math.abs(d) < 1e-9) return `M${ax},${ay}L${cx},${cy}`;
  const ux = ((ax*ax+ay*ay)*(by-cy)+(bx*bx+by*by)*(cy-ay)+(cx*cx+cy*cy)*(ay-by))/d;
  const uy = ((ax*ax+ay*ay)*(cx-bx)+(bx*bx+by*by)*(ax-cx)+(cx*cx+cy*cy)*(bx-ax))/d;
  const r = Math.hypot(ax - ux, ay - uy);
  const cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
  const sweep = cross > 0 ? 1 : 0;
  const a0 = Math.atan2(ay-uy, ax-ux), a2 = Math.atan2(cy-uy, cx-ux);
  let span = sweep ? a2 - a0 : a0 - a2; while (span < 0) span += 2 * Math.PI;
  return `M${ax},${ay}A${r},${r} 0 ${span > Math.PI ? 1 : 0} ${sweep} ${cx},${cy}`;
}

function draw() {
  const svg = document.getElementById("svg");
  svg.innerHTML = "";
  const [x0, y0, x1, y1] = D.bbox || [0, 0, 100, 100];
  const m = 2;
  svg.setAttribute("viewBox", `${x0 - m} ${y0 - m} ${x1 - x0 + 2 * m} ${y1 - y0 + 2 * m}`);
  const g = mk("g", side === "B.Cu" ? { transform: `translate(${x0 + x1} 0) scale(-1 1)` } : {});
  svg.append(g);
  let path = "";
  for (const o of D.outline) {
    if (o.kind === "line") path += `M${o.start}L${o.end}`;
    else if (o.kind === "arc") path += arcPath(o.start, o.mid, o.end);
    else if (o.kind === "rect") path += `M${o.start}H${o.end[0]}V${o.end[1]}H${o.start[0]}Z`;
    else if (o.kind === "circle") { const r = Math.hypot(o.end[0]-o.center[0], o.end[1]-o.center[1]);
      path += `M${o.center[0]-r},${o.center[1]}a${r},${r} 0 1 0 ${2*r},0a${r},${r} 0 1 0 ${-2*r},0`; }
  }
  g.append(mk("path", { d: path, fill: "var(--board)", "fill-rule": "evenodd" }));
  g.append(mk("path", { d: path, class: "edge" }));
  const hl = new Set(sel ? sel.refs : []);
  for (const p of D.pads) {
    if (!(side === "F.Cu" ? p.front : p.back)) continue;
    const t = `translate(${p.x} ${p.y}) rotate(${-p.rot || 0})`;
    const cls = "pad" + (hl.has(p.ref) ? " hl" : "");
    if (p.shape === "circle") g.append(mk("circle", { cx: p.x, cy: p.y, r: p.w / 2, class: cls }));
    else { const rr = p.shape === "oval" ? Math.min(p.w, p.h) / 2 : (p.shape === "roundrect" ? Math.min(p.w, p.h) * 0.25 : 0);
      g.append(mk("rect", { x: -p.w / 2, y: -p.h / 2, width: p.w, height: p.h, rx: rr, transform: t, class: cls })); }
    if (p.drill) g.append(mk("circle", { cx: p.x, cy: p.y, r: p.drill / 2, class: "hole" }));
  }
  for (const f of D.footprints) {
    if (!f.court) continue;
    const onSide = f.side === side;
    if (!onSide && !hl.has(f.ref)) continue;
    const [a, b, c, d] = f.court;
    const r = mk("rect", { x: a, y: b, width: c - a, height: d - b, class: "court fp" + (hl.has(f.ref) ? " hl" : "") });
    r.addEventListener("click", () => pick(D.bom.find(x => x.refs.includes(f.ref)) || null));
    g.append(r);
    if (onSide) { const tx = mk("text", { x: (a + c) / 2, y: (b + d) / 2, class: "ref",
        "font-size": Math.max(0.45, Math.min(1.6, 0.4 * Math.min(c - a, d - b))),
        transform: side === "B.Cu" ? `translate(${a + c} 0) scale(-1 1)` : "" });
      tx.textContent = f.ref; g.append(tx); }
  }
}

function rows() {
  const q = document.getElementById("filter").value.trim().toLowerCase();
  const tb = document.getElementById("rows");
  tb.innerHTML = "";
  let shown = 0, done = 0;
  for (const b of D.bom) {
    const k = b.refs.join(",");
    if (placed[k]) done++;
    if (handOnly && b.fit === "JLCPCB") continue;
    const text = (b.refs.join(" ") + " " + b.value + " " + b.footprint + " " + b.lcsc + " " + b.description).toLowerCase();
    if (q && !q.split(/[\s,]+/).every(w => text.includes(w))) continue;
    shown++;
    const tr = document.createElement("tr");
    tr.className = "row" + (placed[k] ? " placed" : "") + (sel === b ? " sel" : "");
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = !!placed[k];
    cb.addEventListener("click", ev => { ev.stopPropagation(); placed[k] = cb.checked; save(); rows(); });
    const td = document.createElement("td"); td.append(cb); tr.append(td);
    const cell = (html, title) => { const c = document.createElement("td"); c.innerHTML = html; if (title) c.title = title; tr.append(c); };
    const esc = s => String(s).replace(/[&<>"]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
    cell(`<b>${esc(b.refs.join(", "))}</b>` + (b.back ? ' <span class="tag">bottom</span>' : ""));
    cell(esc(b.value), b.description);
    cell(`<span class="muted">${esc(b.footprint)}</span>` + (b.lcsc ? `<br><span class="muted">${esc(b.lcsc)}</span>` : ""));
    cell(`<span class="tag ${b.fit === "JLCPCB" ? "" : "hand"}">${b.fit}${b.tht && b.fit !== "JLCPCB" ? " · through-hole" : ""}</span>`);
    tr.addEventListener("click", () => pick(sel === b ? null : b));
    tb.append(tr);
  }
  document.getElementById("count").textContent = `${done}/${D.bom.length} lines done`;
}

function pick(b) {
  sel = b;
  if (b) { const f = D.footprints.find(x => b.refs.includes(x.ref)); if (f && f.side !== side) setSide(f.side); }
  draw(); rows();
}
function setSide(s) {
  side = s;
  document.getElementById("front").classList.toggle("on", s === "F.Cu");
  document.getElementById("back").classList.toggle("on", s === "B.Cu");
  draw();
}
document.getElementById("front").onclick = () => setSide("F.Cu");
document.getElementById("back").onclick = () => setSide("B.Cu");
document.getElementById("hand").onclick = e => { handOnly = !handOnly; e.target.classList.toggle("on", handOnly); rows(); };
document.getElementById("filter").oninput = rows;
draw(); rows();
</script></body></html>
"""
