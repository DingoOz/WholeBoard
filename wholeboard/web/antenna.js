// Antennas: the placement check's plan view, the openEMS simulation and the VNA measurements under the bring-up
// guide. Loaded before app.js, which calls these from its renderers; el, card, table, api and findings are app.js's.
"use strict";

const SVGNS = "http://www.w3.org/2000/svg";
const sv = (tag, attrs = {}, ...kids) => {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
  for (const k of kids.flat()) if (k) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
};

const ANTENNA = {
  // The board around each antenna: outline, parts, the antenna area, its keep-out and what the check found.
  plan(row, geo) {
    const box = row.keepout || row.antenna;
    const reach = Math.max(8, Math.min(30, row.metal_mm || 20));
    let [x0, y0, x1, y1] = [box[0] - reach, box[1] - reach, box[2] + reach, box[3] + reach];
    const w = x1 - x0, h = y1 - y0;
    const s = sv("svg", { viewBox: `${x0} ${y0} ${w} ${h}`, class: "ant-plan", role: "img",
                          "aria-label": `${row.ref}: the antenna and what is around it` });
    const r = (b, cls, extra = {}) => sv("rect", { x: b[0], y: b[1], width: b[2] - b[0], height: b[3] - b[1], class: cls, ...extra });
    if (geo?.outline?.length) s.append(sv("polygon", { points: geo.outline.map(p => p.join(",")).join(" "), class: "ant-board" }));
    for (const p of geo?.parts || []) {
      const c = p.court;
      if (c[2] < x0 || c[0] > x1 || c[3] < y0 || c[1] > y1) continue;
      s.append(r(c, "ant-part" + (p.ref === row.ref ? " self" : "")),
        sv("text", { x: (c[0] + c[2]) / 2, y: (c[1] + c[3]) / 2, class: "ant-label" }, p.ref));
    }
    if (row.keepout) s.append(r(row.keepout, "ant-keepout"));
    s.append(r(row.antenna, "ant-area"));
    for (const f of row.findings || []) if (f.at) s.append(r(f.at, "ant-hit " + f.level, {}), sv("title", {}, f.text));
    if (row.facing) {
      const a = row.antenna, cx = (a[0] + a[2]) / 2, cy = (a[1] + a[3]) / 2, L = Math.min(w, h) / 5;
      s.append(sv("line", { x1: cx, y1: cy, x2: cx + row.facing[0] * L, y2: cy + row.facing[1] * L, class: "ant-arrow" }));
    }
    s.append(sv("text", { x: x0 + 1, y: y0 + Math.max(2.5, h / 30), class: "ant-legend" },
      "orange: antenna · dashed: keep-out · red/amber: findings"));
    return s;
  },

  check(v) {
    const r = v.report, body = el("div", { class: "ant" });
    body.append(el("p", { class: "muted" }, r.note || ""));
    if (r.problems.length) body.append(el("ul", { class: "list-err" }, findings(r.problems, "antenna", "problem")));
    if (r.warnings.length) body.append(el("ul", { class: "list-warn" }, findings(r.warnings, "antenna", "warning")));
    if (!r.problems.length && !r.warnings.length) body.append(el("p", {}, el("b", {}, "Every antenna is placed as its vendor asks.")));
    for (const a of r.antennas) {
      const kind = { module: "module antenna", chip: "chip antenna", connector: "antenna connector" }[a.kind] || a.kind;
      body.append(el("h5", {}, el("span", {}, `${a.ref}: ${kind}`), el("span", { class: "muted" }, a.footprint)));
      const facts = [`${a.band_mhz[0]}–${a.band_mhz[1]} MHz`];
      if (a.edge_mm != null) facts.push(a.edge_mm <= 0 ? `antenna overhangs the edge by ${(-a.edge_mm).toFixed(1)} mm` : `antenna end ${a.edge_mm} mm inside the edge`);
      if (a.ground_cover != null) facts.push(`${Math.round(a.ground_cover * 100)} % ground under the module`);
      if (a.plastic_mm) facts.push(`keep plastic ${a.plastic_mm} mm and metal ${a.metal_mm} mm away`);
      body.append(el("p", {}, facts.join(" · ")));
      if (a.kind !== "connector") body.append(el("div", { class: "ant-plan-wrap" }, ANTENNA.plan(a, v.geo)));
      const src = Object.entries(a.sources || {});
      if (src.length) body.append(el("details", {}, el("summary", {}, "Where the numbers come from"),
        el("ul", { class: "assume" }, src.map(([k, t]) => el("li", {}, el("b", {}, k + ": "), t)))));
    }
    const e = r.enclosure;
    if (e) {
      body.append(el("h5", {}, el("span", {}, "In the enclosure"), el("span", { class: "muted" }, e.material || "")));
      if (e.problems.length) body.append(el("ul", { class: "list-err" }, findings(e.problems, "antenna", "problem")));
      if (e.warnings.length) body.append(el("ul", { class: "list-warn" }, findings(e.warnings, "antenna", "warning")));
      for (const row of e.rows) {
        body.append(table(["Antenna", ...Object.keys(row.distances), "Keep"], [[el("b", {}, row.ref),
          ...Object.values(row.distances).map(d => el("span", { class: d < row.need_mm ? "bad" : "" }, `${d} mm`)), `${row.need_mm} mm`]]));
        if (row.fittings.length) body.append(el("p", { class: "muted" }, "Metal fittings: " +
          row.fittings.map(f => `${f.what} ${f.gap_mm} mm`).join(", ")));
      }
    }
    return card(v.title, body);
  },

  // The openEMS comparison, or the button that starts it.
  sim(v) {
    const body = el("div", { class: "ant" }), res = v.result;
    const run = el("button", { class: "btn" + (res ? "" : " primary"), type: "button" }, res ? "Simulate again" : "Simulate the antenna");
    const status = el("span", { class: "muted" });
    run.onclick = async () => {
      run.disabled = true;
      status.textContent = " Solving with openEMS: about ten minutes for each variant. Follow the log.";
      try { await api(`/api/projects/${S.pid}/simlab/antenna`, { method: "POST", body: {} }); }
      catch (e) { status.textContent = " " + e.message; run.disabled = false; }
    };
    if (!res) {
      body.append(el("p", {}, "Model the antenna, the board's ground plane, nearby metal parts and the enclosure, and solve "
        + "it with openEMS, bare and in the box: how far the box moves the resonance and the return loss (S11)."),
        el("p", { class: "muted" }, "Module antennas are proprietary shapes, so a generic inverted-F stands in for them. "
          + "Compare the results with each other; the VNA measurement is the final word."),
        el("div", { class: "row-actions" }, run, status));
      return card("Antenna simulation (openEMS)", body);
    }
    body.append(el("p", { class: "muted" }, res.note));
    if (res.notes?.length) body.append(el("ul", { class: "assume" }, res.notes.map(n => el("li", {}, n))));
    body.append(table(["", "Resonance", "Best S11", "Worst in band", "−10 dB bandwidth"], res.rows.map(r =>
      [el("b", {}, r.variant), `${r.resonance_mhz} MHz`, `${r.s11_min_db} dB`,
       el("span", { class: r.worst_in_band_db > -10 ? "bad" : "" }, r.worst_in_band_db == null ? "–" : `${r.worst_in_band_db} dB`),
       `${r.bandwidth_10db_mhz} MHz`])));
    const h = el("div");
    body.append(el("h5", {}, `Return loss (S11), band ${res.band_mhz[0]}–${res.band_mhz[1]} MHz`), h);
    const f = res.variants[0].freq_mhz;
    new Plot(h, f, [...res.variants.map(x => ({ name: x.title, values: x.s11_db })),
      { name: "−10 dB", values: f.map(() => -10) }], { ylabel: "dB", xlabel: "MHz", xunit: "MHz", height: 220 });
    const pv = res.variants.find(x => x.pattern);
    if (pv) body.append(el("h5", {}, el("span", {}, `Radiation pattern, ${pv.title[0].toLowerCase() + pv.title.slice(1)} at ${pv.resonance_mhz} MHz`),
      el("span", { class: "muted" }, `directivity ${pv.pattern.directivity_dbi} dBi`)), ANTENNA.pattern(pv.pattern));
    body.append(el("p", { class: "muted" }, `Solved ${v.made || ""}. Parts modelled as metal: ${res.parts_as_metal.join(", ") || "none"}.`),
      el("div", { class: "row-actions" }, run, status));
    return card(v.title, body);
  },

  // Two cuts of the pattern (the board's plane and the vertical plane), as polar plots in dBi.
  pattern(p) {
    const wrap = el("div", { class: "ant-polar" });
    const cut = (title, angles, vals) => {
      const R = 46, lo = -30, hi = Math.max(0, ...vals);
      const rad = g => R * Math.max(0, (g - lo) / (hi - lo));
      const s = sv("svg", { viewBox: "-60 -60 120 128", class: "ant-polar-svg", role: "img", "aria-label": title });
      for (const g of [lo, (lo + hi) / 2, hi]) s.append(sv("circle", { r: rad(g), class: "ant-ring" }),
        sv("text", { x: 2, y: -rad(g) - 1, class: "ant-ring-label" }, `${Math.round(g)}`));
      const pts = angles.map((a, i) => { const t = a * Math.PI / 180, q = rad(vals[i]); return `${(q * Math.sin(t)).toFixed(2)},${(-q * Math.cos(t)).toFixed(2)}`; });
      s.append(sv("polygon", { points: pts.join(" "), class: "ant-lobe" }),
        sv("text", { x: 0, y: 64, class: "ant-polar-title" }, title));
      return s;
    };
    const ti = p.theta_deg.indexOf(90), phis = p.phi_deg;
    if (ti >= 0) wrap.append(cut("Board plane (θ = 90°), dBi", phis, p.dbi[ti]));
    const pi0 = phis.indexOf(0), pi180 = phis.indexOf(180);
    if (pi0 >= 0 && pi180 >= 0) {
      const angles = [...p.theta_deg, ...p.theta_deg.slice(1, -1).reverse().map(t => 360 - t)];
      const vals = [...p.dbi.map(r => r[pi0]), ...p.dbi.slice(1, -1).reverse().map(r => r[pi180])];
      wrap.append(cut("Vertical (φ = 0°/180°), dBi", angles, vals));
    }
    return wrap;
  },

  // Under the bring-up guide: enter what the VNA showed; the report keeps it with the revision.
  measure(v) {
    const box = el("div", { class: "ant-measure" });
    const rows = v.measurements || [];
    box.append(el("h5", {}, "Antenna measurements"));
    if (rows.length) box.append(table(["Antenna", "Where", "Resonance", "Best S11", "Worst in band", "Note", "When"],
      rows.map(m => [el("b", {}, m.ref), m.enclosed ? "in the box" : "outside", m.resonance_mhz != null ? `${m.resonance_mhz} MHz` : "–",
        m.s11_db != null ? `${m.s11_db} dB` : "–", m.worst_in_band_db != null ? `${m.worst_in_band_db} dB` : "–", m.note || "", m.at])));
    const refSel = el("select", { name: "ref" }, v.antennas.map(a => el("option", { value: a.ref }, `${a.ref} (${a.band_mhz[0]}–${a.band_mhz[1]} MHz)`)));
    const num = (name, ph) => el("input", { name, type: "number", step: "any", placeholder: ph, class: "num" });
    const res = num("resonance_mhz", "MHz"), s11 = num("s11_db", "dB"), worst = num("worst_in_band_db", "dB");
    const where = el("select", { name: "enclosed" }, el("option", { value: "1" }, "in the box, lid closed"), el("option", { value: "" }, "outside the box"));
    const note = el("input", { name: "note", type: "text", placeholder: "Note (optional)", maxlength: 300 });
    const save = el("button", { class: "btn", type: "submit" }, "Save");
    const msg = el("span", { class: "muted" });
    const form = el("form", { class: "ant-form" },
      el("label", {}, "Antenna ", refSel), el("label", {}, "Measured ", where), el("label", {}, "Resonance ", res),
      el("label", {}, "Best S11 ", s11), el("label", {}, "Worst in band ", worst), note, save, msg);
    form.onsubmit = async e => {
      e.preventDefault();
      save.disabled = true; msg.textContent = "";
      try {
        await api(`/api/projects/${S.pid}/antenna/measurements`, { method: "POST", body: {
          ref: refSel.value, enclosed: !!where.value, resonance_mhz: res.value, s11_db: s11.value,
          worst_in_band_db: worst.value, note: note.value } });
        msg.textContent = "Saved with this revision.";
      } catch (err) { msg.textContent = err.message; }
      finally { save.disabled = false; }
    };
    box.append(form);
    return box;
  },
};
window.ANTENNA = ANTENNA;
