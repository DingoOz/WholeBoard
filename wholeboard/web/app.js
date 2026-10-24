// WholeBoard front end.
"use strict";

const $ = s => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(k.nodeType ? k : String(k));
  return e;
};
const api = async (path, opts = {}) => {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
                                body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (r.status === 401 && path !== "/api/login") { location.href = "/login"; throw new Error("log in first"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.detail || data.errors?.join("\n") || r.statusText), { data });
  return data;
};
const money = v => (v === null || v === undefined) ? "–" : "$" + Number(v).toFixed(2);

const EXAMPLES = [
  ["USB-C LED board", "A small USB-C powered board (power only) with a 3.3 V regulator, a power LED and three user LEDs driven from a 4-pin header. As small as practical."],
  ["ESP32-C3 sensor", "An ESP32-C3 sensor node: USB-C for power and programming, BME280 temperature/humidity/pressure sensor on I2C, a status LED, a boot and reset button, and a 4-pin I2C expansion header. About 40 x 30 mm."],
  ["555 blinker", "A classic NE555 astable LED blinker running from a 9 V battery connector, about 1 Hz, with two alternating LEDs."],
  ["RP2040 breakout", "An RP2040 microcontroller board with USB-C, 16 MB QSPI flash, 12 MHz crystal, 3.3 V LDO, reset and boot buttons, and all GPIO on two 2.54 mm headers. Feather-like size."],
];

const S = { status: null, steps: [], projects: [], pid: null, project: null, ws: null, visuals: {},
            selStep: null, follow: true, lastSeq: 0, logs: [], questions: {} };

// ------------------------------------------------------------------ status and home
async function loadStatus(refresh = false) {
  S.status = await api("/api/status" + (refresh ? "?refresh=1" : ""));
  S.steps = S.status.steps;
  const st = S.status, chips = $("#statusChips");
  chips.innerHTML = "";
  const chip = (ok, text, title) => chips.append(el("span", { class: "chip " + (ok ? "ok" : "bad"), title }, text));
  chip(!!st.kicad_version, st.kicad_version ? `KiCad ${st.kicad_version}` : "KiCad missing", st.kicad_cli || "kicad-cli not found");
  chip(!!st.tracemaker_version, st.tracemaker_version || "TraceMaker missing", st.tracemaker || "tracemaker not found");
  chip(!!st.freecad_version, st.freecad_version ? `FreeCAD ${st.freecad_version}` : "FreeCAD missing", st.freecad || "FreeCAD not found (needed for enclosures)");
  chip(!!st.ngspice_version, st.ngspice_version ? `ngspice ${st.ngspice_version.replace(" (Docker)", "")}` : "ngspice missing", st.ngspice || "ngspice not found (needed for simulation)");
  chip(st.libs.footprints > 0, st.libs.footprints ? `${st.libs.footprints.toLocaleString()} footprints` : "No KiCad libraries", st.libs.footprint_dir || "");
  chip(st.ai.ready, st.ai.label, st.ai.ready ? "AI configured" : "AI not configured: open Settings");
  renderSetup();
}

function renderSetup() {
  const st = S.status, box = $("#setup");
  const row = (ok, k, v, fix) => el("li", {}, el("span", { class: "chip " + (ok ? "ok" : "bad") }, ok ? "ready" : "needed"),
                                       el("span", { class: "k" }, k), el("span", { class: "v" }, v), fix || "");
  const open = el("button", { class: "btn ghost", type: "button", onclick: () => openSettings() }, "Settings");
  box.innerHTML = "";
  box.append(el("h3", {}, "Workbench setup"), el("ul", {},
    row(!!st.kicad_version, "KiCad", st.kicad_version ? `${st.kicad_version} · ${st.kicad_cli}` : "Install KiCad 9 or 10 (kicad-cli is used for checks, renders and fab files)."),
    row(!!st.tracemaker_version, "TraceMaker", st.tracemaker_version ? `${st.tracemaker_version} · ${st.tracemaker}` : "Build TraceMaker (Linux/macOS) or run it in WSL on Windows, then set its path."),
    row(st.libs.footprints > 0, "KiCad libraries", st.libs.footprints ? `${st.libs.footprints.toLocaleString()} footprints · ${st.libs.footprint_dir}` : "Not found: download them in Settings."),
    row(!!st.freecad_version, "FreeCAD", st.freecad_version ? `${st.freecad_version} · ${st.freecad.split(" ")[0]}` : "Needed for enclosures: install it or download it in Settings."),
    row(!!st.ngspice_version, "ngspice", st.ngspice_version ? `${st.ngspice_version} · ${st.ngspice}` : "Needed for circuit simulation: run the installer, or install it in Settings."),
    row(st.ai.ready, "AI", st.ai.ready ? st.ai.label : "Add a Claude API key or a local OpenAI-compatible server.", st.ai.ready ? "" : open),
  ));
}

function renderExamples() {
  const box = $("#examples");
  for (const [label, text] of EXAMPLES)
    box.append(el("button", { class: "chip", type: "button", onclick: () => { $("#prompt").value = text; $("#prompt").focus(); } }, label));
}

// ------------------------------------------------------------------ projects
async function loadProjects() {
  S.projects = await api("/api/projects");
  const shown = S.projects.filter(p => !p.hidden), cleared = S.projects.length - shown.length;
  const ul = $("#projectList");
  ul.innerHTML = "";
  if (!shown.length) ul.append(el("li", { class: "muted" }, cleared ? "List cleared" : "No boards yet"));
  $("#clearListBtn").hidden = !shown.length;
  $("#restoreListBtn").hidden = !cleared;
  $("#restoreListBtn").textContent = `Show ${cleared} cleared board${cleared === 1 ? "" : "s"}`;
  for (const p of shown) {
    ul.append(el("li", { class: p.id === S.pid ? "on" : "", onclick: () => openProject(p.id) },
      el("span", { class: "t", title: p.title }, p.title),
      el("span", { class: "s" }, el("span", {}, p.status || ""), el("span", {}, p.total ? money(p.total) : ""))));
  }
}

function showHome() {
  if (S.ws) { S.ws.onclose = null; S.ws.close(); S.ws = null; }
  S.pid = null;
  $("#home").hidden = false; $("#workspace").hidden = true;
  history.replaceState(null, "", "#");
  loadProjects();
}

async function openProject(pid) {
  if (S.ws) { S.ws.onclose = null; S.ws.close(); S.ws = null; }
  S.pid = pid; S.visuals = {}; S.selStep = null; S.follow = true; S.lastSeq = 0; S.logs = []; S.questions = {};
  $("#home").hidden = true; $("#workspace").hidden = false;
  $("#chat").innerHTML = ""; $("#log").textContent = "";
  history.replaceState(null, "", "#" + pid);
  const data = await api(`/api/projects/${pid}`);
  S.project = data.state;
  $("#wsTitle").textContent = S.project.title;
  showUsage(S.project.usage);
  renderStepper();
  setStatus(S.project.status, S.project.status_note);
  for (const e of data.events) handleEvent(e, true);
  scrollChat();
  if (!S.selStep) selectStep(lastStepWithVisual() || "brief", false);
  else renderStage();
  loadProjects();
  connect();
}

function connect() {
  const pid = S.pid;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/projects/${pid}/ws?after=${S.lastSeq}`);
  S.ws = ws;
  ws.onmessage = m => handleEvent(JSON.parse(m.data), false);
  ws.onclose = e => { if (e.code === 4401) { location.href = "/login"; return; } if (S.pid === pid) setTimeout(() => { if (S.pid === pid) connect(); }, 1500); };
}

// ------------------------------------------------------------------ events
function handleEvent(e, replay) {
  if (e.seq <= S.lastSeq) return;
  S.lastSeq = e.seq;
  switch (e.type) {
    case "chat": addMsg(e.role, e.text); break;
    case "tool": addMsg("tool", e.text); break;
    case "question": addQuestion(e, replay); break;
    case "answer": markAnswered(e.id, e.text); break;
    case "finish": addFinish(e.text); break;
    case "status": setStatus(e.status, e.note); if (!replay) loadProjects(); break;
    case "usage": showUsage(e); break;
    case "stock": S.project.stock = e; if (S.selStep === "quote") renderStage(); break;
    case "step":
      S.project.steps[e.key] = { status: e.status, detail: e.detail };
      updateStep(e.key);
      if (!replay && e.status === "running" && S.follow) selectStep(e.key, false);
      break;
    case "log": addLog(e.step, e.text); break;
    case "simlab": if (!replay && e.action === "video") { S.simlabVideo = true; S.follow = false; selectStep("sim", true); } break;
    case "visual":
      (S.visuals[e.step] ||= {})[e.kind] = e;
      if (e.kind === "viewer" && !replay) S.liveViewer = true;
      if (!replay && S.follow) selectStep(e.step, false);
      else if (S.selStep === e.step) renderStage();
      break;
  }
  if (!replay) scrollChat();
}

function scrollChat() { const c = $("#chat"); c.scrollTop = c.scrollHeight; }

function chatAppend(node) {
  const dots = $("#typing");
  if (dots) $("#chat").insertBefore(node, dots); else $("#chat").append(node);
}

// Minimal, safe Markdown for AI messages: escape first, then bold, code and bullet lists.
function md(text) {
  const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = s => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [];
  let list = false;
  for (const line of text.split("\n")) {
    const m = line.match(/^\s*[-*•]\s+(.*)$/);
    if (m) { if (!list) { out.push("<ul>"); list = true; } out.push("<li>" + inline(m[1]) + "</li>"); continue; }
    if (list) { out.push("</ul>"); list = false; }
    out.push(line.trim() ? "<p>" + inline(line) + "</p>" : "");
  }
  if (list) out.push("</ul>");
  const div = document.createElement("div");
  div.className = "md";
  div.innerHTML = out.join("");
  return div;
}

function addMsg(role, text) {
  chatAppend(el("div", { class: "msg " + role }, role === "assistant" ? md(text) : text));
}

function addQuestion(e, replay) {
  const box = el("div", { class: "question", id: "q-" + e.id });
  const send = text => { if (!text.trim()) return; api(`/api/projects/${S.pid}/answer`, { method: "POST", body: { id: e.id, text } }); };
  const input = el("input", { placeholder: "Type your answer…" });
  input.addEventListener("keydown", ev => { if (ev.key === "Enter") { ev.preventDefault(); send(input.value); } });
  box.append(el("div", { class: "q" }, e.question));
  if (e.options?.length) box.append(el("div", { class: "opts" }, e.options.map(o => el("button", { class: "chip", type: "button", onclick: () => send(o) }, o))));
  box.append(el("div", { class: "free" }, input, el("button", { class: "btn", type: "button", onclick: () => send(input.value) }, "Answer")));
  chatAppend(box);
  S.questions[e.id] = box;
  if (!replay) setTimeout(() => input.focus(), 50);
}

function markAnswered(id, text) {
  const box = S.questions[id];
  if (!box) return;
  box.classList.add("answered");
  box.querySelectorAll("button, input").forEach(b => b.disabled = true);
  box.append(el("div", { class: "ans" }, "↳ " + text));
}

function addFinish(text) {
  chatAppend(el("div", { class: "finish" }, el("b", {}, "Board ready"), md(text)));
}

function showUsage(u) {
  const chip = $("#wsUsage");
  if (!u || !u.turns) { chip.hidden = true; return; }
  const k = n => n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(n);
  const tokens = (u.input || 0) + (u.cache_read || 0) + (u.cache_write || 0);
  chip.hidden = false;
  chip.textContent = `AI ${k(tokens)} in · ${k(u.output || 0)} out` + (u.priced ? ` · $${(u.cost || 0).toFixed(2)}` : "");
  chip.title = `${u.turns} AI turns with ${u.model || "the AI"}\nInput ${(u.input || 0).toLocaleString()} tokens, cache reads ${(u.cache_read || 0).toLocaleString()}, cache writes ${(u.cache_write || 0).toLocaleString()}\nOutput ${(u.output || 0).toLocaleString()} tokens`
    + (u.priced ? `\nAbout $${(u.cost || 0).toFixed(4)} at list prices` : "\nPrice unknown for this model: set it in Settings")
    + (Object.keys(u.by_model || {}).length > 1 ? "\n" + Object.entries(u.by_model).map(([m, x]) => `${m}: ${x.turns} turns, $${x.cost.toFixed(3)}`).join("\n") : "");
}

function setStatus(status, note) {
  S.project.status = status;
  const pill = $("#wsStatus");
  pill.className = "pill " + (status || "");
  pill.textContent = status || "";
  $("#wsNote").textContent = note || "";
  const busy = status === "running" || status === "waiting";
  $("#stopBtn").hidden = !busy;
  $("#deleteBtn").hidden = busy;
  let dots = $("#typing");
  if (status === "running") {
    if (!dots) dots = el("div", { id: "typing", class: "typing" }, el("span"), el("span"), el("span"));
    dots.title = note || "";
    $("#chat").append(dots);
  } else if (dots) dots.remove();
  if (!busy && S.liveViewer) { S.liveViewer = false; if (S.selStep === "route") renderStage(); }
}

function addLog(step, text) {
  S.logs.push([step, text]);
  if (S.logs.length > 4000) S.logs.splice(0, 1000);
  if (!S.selStep || S.selStep === step) {
    const pre = $("#log");
    pre.textContent += text + "\n";
    if (pre.textContent.length > 200000) pre.textContent = pre.textContent.slice(-150000);
    pre.scrollTop = pre.scrollHeight;
  }
}

// ------------------------------------------------------------------ stepper and stage
function renderStepper() {
  const ol = $("#stepper");
  ol.innerHTML = "";
  S.steps.forEach((s, i) => {
    const st = S.project.steps[s.key] || { status: "pending" };
    ol.append(el("li", { id: "step-" + s.key, class: st.status, onclick: () => { S.follow = false; selectStep(s.key, true); } },
      el("span", { class: "n" }, `${i + 1}`), el("span", { class: "t", title: s.title }, s.short || s.title), el("span", { class: "d", title: st.detail || "" }, st.detail || "")));
  });
}

function updateStep(key) {
  const li = $("#step-" + key);
  if (!li) return;
  const st = S.project.steps[key];
  li.className = st.status + (S.selStep === key ? " sel" : "");
  li.querySelector(".d").textContent = st.detail || "";
  li.querySelector(".d").title = st.detail || "";
}

function lastStepWithVisual() {
  let last = null;
  for (const s of S.steps) if (S.visuals[s.key]) last = s.key;
  return last;
}

function selectStep(key, user) {
  S.selStep = key;
  document.querySelectorAll(".stepper li").forEach(li => li.classList.toggle("sel", li.id === "step-" + key));
  if (user && key === lastStepWithVisual()) S.follow = true;
  renderStage();
  const pre = $("#log");
  pre.textContent = S.logs.filter(([s]) => s === key).map(([, t]) => t).join("\n");
  $("#logStep").textContent = "· " + (S.steps.find(s => s.key === key)?.title || "");
}

const STEP_HELP = {
  brief: "The designer reads your request and asks questions in the chat if it needs to.",
  parts: "The designer searches JLCPCB's parts library and KiCad's footprints and symbols.",
  netlist: "The design is checked: footprints exist, pads are right, nets make sense, parts are in stock.",
  schematic: "A KiCad schematic is drawn from the design and checked with KiCad's electrical rules check (ERC).",
  sim: "ngspice simulates the circuit: rail voltages, supply current and battery life, oscillators, and every part against its ratings.",
  review: "The AI looks at the 3D renders like a reviewer would: parts, connectors, LEDs and buttons where they should be.",
  layout: "A KiCad board is created with the outline, mounting holes and footprints.",
  place: "TraceMaker places the parts on the board.",
  route: "TraceMaker routes every connection.",
  drc: "A ground pour is added, KiCad's design rule check runs, and tracks are checked for heating, voltage drop and decoupling distance.",
  outputs: "3D renders and the Gerber, drill, BOM and placement files for JLCPCB.",
  enclosure: "Once the board is finished, the designer plans a 3D-printable box around it and FreeCAD builds and checks it.",
  quote: "The full JLCPCB quote: PCB, assembly, parts and shipping.",
};

function renderStage() {
  const stage = $("#stage");
  stage.innerHTML = "";
  const key = S.selStep;
  const vis = S.visuals[key] || {};
  const order = ["import", "viewer", "board", "svg", "erc", "sim", "power", "netlist", "drc", "checks", "impedance", "images", "files", "panel", "report", "bringup", "firmware", "review", "case", "enclosure", "quote"];
  const kinds = Object.keys(vis).sort((a, b) => order.indexOf(a) - order.indexOf(b));
  if (key === "sim" && vis.sim?.result?.ran && S.visuals.outputs?.files) kinds.unshift("simlab");
  if (key === "route" && !S.liveViewer) { const i = kinds.indexOf("viewer"); if (i >= 0) kinds.splice(i, 1); }
  if (!kinds.length) {
    const st = S.project.steps[key] || {};
    stage.append(el("div", { class: "empty" }, el("p", {}, STEP_HELP[key] || ""), st.detail ? el("p", {}, el("b", {}, st.detail)) : ""));
    if (key === "brief" || key === "parts") stage.lastChild.append(el("p", { class: "muted" }, "Follow the conversation on the left."));
    return;
  }
  for (const kind of kinds) {
    const v = vis[kind] || {};
    const fn = RENDER[kind];
    if (!fn) continue;
    const node = fn(v);
    const first = node.nodeType === 11 ? node.firstElementChild : node;     // some renderers return several cards
    if (first?.dataset) first.dataset.kind = kind;
    stage.append(node);
  }
}

function card(title, body, opts = {}) {
  return el("div", { class: "vis" + (opts.fill ? " fill" : "") }, el("h4", {}, el("span", {}, title), opts.right || ""), el("div", { class: "body" }, body));
}

const RENDER = {
  board(v) {
    const host = el("div");
    const c = card(v.title, host, { fill: true });
    fetch(v.url).then(r => r.json()).then(geo => {
      const isPlace = S.selStep === "place" || S.selStep === "layout";
      new BoardView(host, geo, { ratsnest: isPlace, courtyards: S.selStep === "layout" });
    });
    return c;
  },
  viewer(v) {
    // The viewer runs on the WholeBoard machine, so point at whatever host this page was loaded from.
    const url = v.port ? `${location.protocol}//${location.hostname}:${v.port}/` + (v.token ? `?token=${encodeURIComponent(v.token)}` : "") : v.url;
    const frame = el("iframe", { class: "viewer-frame", src: url, title: "TraceMaker live viewer" });
    return card(v.title, frame, { fill: true, right: el("a", { href: url, target: "_blank" }, "open in new tab") });
  },
  svg(v) {
    const box = el("div", { class: "zoomimg" });
    const img = el("img", { src: v.url, alt: v.title });
    box.append(img);
    zoomable(box, img);
    const open = el("a", { href: v.url, target: "_blank" }, "open SVG");
    if (!v.pages?.length) return card(v.title, box, { fill: true, right: open });
    // a hierarchical schematic: one tab per sheet
    const tabs = el("div", { class: "sheet-tabs", role: "tablist" });
    const show = (pg, b) => {
      img.src = pg.url; open.href = pg.url;
      tabs.querySelectorAll("button").forEach(x => x.setAttribute("aria-selected", x === b ? "true" : "false"));
    };
    v.pages.forEach((pg, i) => {
      const b = el("button", { type: "button", role: "tab", "aria-selected": i === 0 ? "true" : "false",
        title: `${pg.parts ?? "?"} parts` }, pg.title);
      b.onclick = () => show(pg, b);
      tabs.append(b);
    });
    return card(v.title, el("div", { class: "sheet-wrap" }, tabs, box), { fill: true, right: open });
  },
  netlist(v) {
    const body = el("div");
    if (v.errors?.length) body.append(el("b", {}, "Problems the designer must fix"), el("ul", { class: "list-err" }, v.errors.map(x => el("li", {}, x))));
    if (v.warnings?.length) body.append(el("b", {}, "Notes"), el("ul", { class: "list-warn" }, v.warnings.map(x => el("li", {}, x))));
    body.append(el("table", {}, el("thead", {}, el("tr", {}, ["Ref", "Value", "Footprint", "LCSC", "Description"].map(h => el("th", {}, h)))),
      el("tbody", {}, v.components.map(c => el("tr", {}, el("td", {}, el("b", {}, c.ref)), el("td", {}, c.value || ""),
        el("td", { class: "pins" }, c.footprint), el("td", {}, c.lcsc ? el("a", { href: `https://jlcpcb.com/partdetail/${c.lcsc}`, target: "_blank" }, c.lcsc) : "–"),
        el("td", { class: "muted" }, c.description || ""))))));
    body.append(el("h5", {}, `Nets (${v.nets.length})`), el("table", {}, el("tbody", {}, v.nets.map(n =>
      el("tr", {}, el("td", {}, el("b", {}, n.name), n.class === "power" ? el("span", { class: "tag preferred", style: "margin-left:6px" }, "power") : ""),
        el("td", { class: "pins" }, n.pins.join("  ")))))));
    return card(v.title, body);
  },
  drc(v) {
    const r = v.report;
    const stat = (val, label, good) => el("div", { class: "stat " + (good ? "good" : "bad") }, el("div", { class: "v" }, val), el("div", { class: "l" }, label));
    const body = el("div", {}, el("div", { class: "stats" },
      stat(r.errors, "errors", r.errors === 0), stat(r.unconnected, "unconnected", r.unconnected === 0),
      el("div", { class: "stat" }, el("div", { class: "v" }, r.warnings), el("div", { class: "l" }, "warnings"))));
    const list = [...r.error_list, ...r.unconnected_list];
    if (list.length) body.append(el("ul", { class: "list-err" }, list.map(x => el("li", {}, `${x.description}: ${x.items.join(" ↔ ")}`))));
    if (r.warning_list?.length) body.append(el("details", {}, el("summary", {}, `Warnings (${r.warnings})`),
      el("ul", { class: "list-warn" }, r.warning_list.map(x => el("li", {}, `${x.description}: ${x.items.join(" ↔ ")}`)))));
    return card(v.title, body);
  },
  images(v) {
    return card(v.title, el("div", {}, el("div", { class: "gallery" }, v.images.map(i => el("figure", {}, el("a", { href: i.url, target: "_blank" }, el("img", { src: i.url, alt: i.view })), el("figcaption", {}, i.view)))),
      v.note ? el("p", { class: "muted" }, v.note) : ""));
  },
  panel(v) {
    const host = el("div");
    fetch(v.url).then(r => r.json()).then(geo => new BoardView(host, geo, {}));
    return card(v.title, el("div", {}, el("div", { class: "panel-view" }, host),
      el("div", { class: "files" }, v.files.map(f => el("a", { class: "btn", href: f.url, download: f.name }, "⬇ " + f.name))),
      el("p", { class: "muted" }, v.note || "")));
  },
  files(v) {
    const body = el("div", {}, el("div", { class: "files" }, v.files.map(f => el("a", { class: "btn", href: f.url, download: f.name }, "⬇ " + f.name))),
      el("p", { class: "muted" }, v.note || ""), el("p", { class: "pins" }, v.folder || ""));
    if (v.rotations?.length) body.append(el("details", {}, el("summary", {}, `CPL rotation corrections (${v.rotations.length})`),
      table(["Ref", "Footprint", "KiCad", "JLCPCB", "From"], v.rotations.map(r => [el("b", {}, r.ref), el("span", { class: "pins" }, r.package),
        `${r.kicad_rotation}°`, `${r.jlc_rotation}°` + (r.dx_mm || r.dy_mm ? ` (moved ${r.dx_mm}, ${r.dy_mm} mm)` : ""), r.source]))));
    return card(v.title, body, { right: el("a", { href: "https://cart.jlcpcb.com/quote", target: "_blank" }, "Order at JLCPCB ↗") });
  },
  quote(v) { return renderQuote(v); },
  power(v) { return renderPower(v); },
  impedance(v) {
    const r = v.report, body = el("div");
    if (r.warnings.length) body.append(el("ul", { class: "list-warn" }, r.warnings.map(x => el("li", {}, x))));
    for (const g of r.groups) {
      const bus = g.kind === "bus";
      const limits = [g.max_length_mm != null && `under ${g.max_length_mm} mm long`, g.max_vias != null && `at most ${g.max_vias} vias`,
        g.max_stub_mm != null && `stubs under ${g.max_stub_mm} mm`, g.max_match_mm != null && `matched within ${g.max_match_mm} mm`].filter(Boolean);
      body.append(el("h5", {}, el("span", {}, `${g.nets.join(" / ")}: ` + (bus ? "length-matched bus" : `${g.ohms} Ω ${g.kind === "diff" ? "differential pair" : "single-ended"}`)),
        el("span", { class: "muted" }, g.profile_title || g.stackup)),
        bus ? "" : el("p", { class: "muted" }, g.practical ? `Target ${g.width_mm} mm wide` + (g.gap_mm ? `, ${g.gap_mm} mm apart` : "") +
          ` (≈${g.achieved} Ω over ${g.h_mm} mm of dielectric, εr ${g.er})` : g.note || ""),
        limits.length ? el("p", { class: "muted" }, "Limits: " + limits.join(", ")) : "",
        table(["Net", "Length", "Narrowest", "Vias", "Longest stub"], g.members.map(m => [el("b", {}, m.net), `${m.length_mm} mm`,
          m.width_mm != null ? `${m.width_mm} mm` : "–", m.vias, m.stub_mm ? `${m.stub_mm} mm` : "–"])),
        g.skew_mm != null ? el("p", {}, `Length difference ${g.skew_mm} mm (limit ${g.max_skew_mm} mm)`) : "",
        g.match_mm != null ? el("p", {}, `Longest minus shortest ${g.match_mm} mm` + (g.max_match_mm != null ? ` (limit ${g.max_match_mm} mm)` : "")) : "");
    }
    if (r.order_note) body.append(el("p", {}, el("b", {}, r.order_note)));
    return card(v.title, body);
  },
  mains(v) {
    const r = v.report, q = r.requirements || {}, body = el("div");
    if (q.working_v) body.append(el("p", {}, `${q.working_v} V working (${q.peak_v} V peak, ${q.impulse_v} V transients). ` +
      `Mains nets to other mains-side nets: ${q.functional_outer_mm} mm (inner layers ${q.functional_inner_mm} mm). ` +
      (r.config?.isolated ? `Mains side to low voltage: ${q.clearance_mm} mm clearance and ${q.creepage_mm} mm creepage (reinforced).` :
        "Not isolated: the whole board is live.")));
    if (r.barrier?.length) body.append(el("p", { class: "muted" }, "Isolation barrier: " + r.barrier.map(b => b.ref).join(", ") +
      (r.slots ? `; ${r.slots} milled slot${r.slots > 1 ? "s" : ""}` : "")));
    if (r.problems.length) body.append(el("ul", { class: "list-err" }, r.problems.map(x => el("li", {}, x))));
    else body.append(el("p", {}, el("b", {}, "Every spacing passes.")));
    if (r.rows?.length) body.append(el("details", {}, el("summary", {}, `Closest approaches (${r.rows.length})`),
      table(["", "From", "To", "Clearance", "Creepage"], r.rows.map(x => [x.ok ? "✓" : "✗", x.a, x.b,
        `${x.clearance_mm} / ${x.need_mm} mm`, x.need_creepage_mm != null ? `${x.creepage_mm} / ${x.need_creepage_mm} mm` : "–"]))));
    if (r.note) body.append(el("p", { class: "muted" }, r.note));
    return card(v.title, body);
  },
  bringup(v) {
    const body = el("div", {}, el("p", { class: "muted" }, "Work through this when the boards arrive." +
      (v.test_points?.length ? ` Test pads: ${v.test_points.map(t => `${t.ref} = ${t.net}`).join(", ")}.` : "")));
    v.steps.forEach((st, i) => {
      body.append(el("h5", {}, `${i + 1}. ${st.title}`), el("ul", { class: "checklist" }, st.items.map(x => el("li", {}, el("label", {}, el("input", { type: "checkbox" }), " ", x)))));
      if (st.table?.rows?.length) body.append(table(st.table.head, st.table.rows));
    });
    return card(v.title, body, { right: el("a", { href: v.url, download: v.name }, "⬇ " + v.name) });
  },
  report(v) {
    const btn = el("button", { class: "btn", type: "button" }, "Make it again");
    btn.onclick = async () => { btn.disabled = true; try { await api(`/api/projects/${S.pid}/report`, { method: "POST" }); } finally { btn.disabled = false; } };
    return card(v.title, el("div", { class: "row-actions" }, el("a", { class: "btn primary", href: v.url, target: "_blank" }, "Open " + v.name), btn,
      el("span", { class: "muted" }, `Summary, renders, cost, BOM, simulation, power, enclosure, bring-up checklist and the schematic. Made ${v.made || ""}.`)));
  },
  case(v) {
    const r = v.result, c = r.case;
    const body = el("div", {}, el("p", {}, `Inside ${c.L} × ${c.W} × ${c.H} mm; the board sits at (${c.ax}, ${c.ay}) mm on ${c.standoff} mm standoffs, with ${r.headroom_mm} mm above the tallest part.`));
    if (r.problems.length) body.append(el("ul", { class: "list-err" }, r.problems.map(x => el("li", {}, x))));
    if (r.warnings.length) body.append(el("ul", { class: "list-warn" }, r.warnings.map(x => el("li", {}, x))));
    if (r.cutouts.length) body.append(table(["Face", "Part", "Hole", "Position (from the face's bottom-left)"], r.cutouts.map(k =>
      [k.face, k.ref, k.shape === "circle" ? `⌀${k.d} mm` : `${k.w} × ${k.h} mm`, k.shape === "circle" ? `centre ${k.u}, ${k.v} mm` : `corner ${k.u}, ${k.v} mm`])));
    body.append(el("div", { class: "files" }, v.files.map(f => el("a", { class: "btn", href: f.url, target: "_blank" }, f.label))));
    if (r.thermal) body.append(boxThermal(r.thermal));
    return card(v.title, body, { right: el("a", { href: v.page, target: "_blank" }, "Print the templates ↗") });
  },
  firmware(v) {
    const f = v.firmware;
    return card(v.title, el("div", {}, firmwareBuild(v, f),
      el("p", { class: "muted" }, `A PlatformIO project (Arduino framework, env ${f.env}). include/pins.h names each pin after its net; src/main.cpp blinks the LEDs, reads buttons and analog inputs and scans I²C. Upload with: pio run -t upload`),
      f.pins.length ? table(["Macro", "Pin", "Pad", "Symbol pin", "Net", "Use", "Connects to"],
        f.pins.map(r => [el("code", {}, r.macro), String(r.gpio), r.pad, r.pin_name, r.net, r.role.replace("_", " "), r.connects.join(", ")])) : el("p", {}, "No GPIO pins found."),
      f.notes?.length ? el("ul", { class: "assume" }, f.notes.map(n => el("li", {}, n))) : ""),
      { right: el("a", { href: v.url, download: v.name }, "⬇ " + v.name) });
  },
  import(v) {
    const r = v.report, list = (title, xs, cls) => xs?.length ? [el("h5", {}, title), el("ul", { class: "assume " + (cls || "") }, xs.map(x => el("li", {}, x)))] : [];
    return card(v.title, el("div", {},
      el("p", {}, `${r.counts.components} parts and ${r.counts.nets} nets from `, el("b", {}, [r.files.schematic, r.files.board].filter(Boolean).join(" and ")), "."),
      ...list("Notes", r.notes), ...list("Needs fixing", r.errors, "bad"), ...list("Warnings", r.warnings)));
  },
  enclosure(v) { return renderEnclosure(v); },
  sim(v) { return renderSim(v); },
  simlab() { return renderSimLab(); },
  erc(v) {
    const r = v.report;
    const stat = (val, label, good) => el("div", { class: "stat " + (good ? "good" : "bad") }, el("div", { class: "v" }, val), el("div", { class: "l" }, label));
    const body = el("div", {}, el("div", { class: "stats" }, stat(r.errors.length, "errors", !r.errors.length),
      el("div", { class: "stat" }, el("div", { class: "v" }, r.warnings.length), el("div", { class: "l" }, "warnings")),
      el("div", { class: "stat" }, el("div", { class: "v" }, r.ignored), el("div", { class: "l" }, "library notes ignored"))));
    const item = x => el("li", {}, `${x.description}`, x.items.length ? el("div", { class: "muted" }, x.items.join(" · ")) : "");
    if (r.errors.length) body.append(el("ul", { class: "list-err" }, r.errors.map(item)));
    if (r.warnings.length) body.append(el("ul", { class: "list-warn" }, r.warnings.map(item)));
    if (!r.errors.length && !r.warnings.length) body.append(el("p", { class: "muted" }, "No conflicting outputs, floating inputs or wiring problems."));
    return card(v.title, body);
  },
  checks(v) {
    const r = v.report, body = el("div");
    if (r.problems.length) body.append(el("ul", { class: "list-err" }, r.problems.map(x => el("li", {}, x))));
    if (r.warnings.length) body.append(el("ul", { class: "list-warn" }, r.warnings.map(x => el("li", {}, x))));
    if (r.tracks.length) {
      const m = r.method === "ipc2221" ? ["IPC-2221", "ipc2152", "IPC-2152"] : ["IPC-2152", "ipc2221", "IPC-2221"];
      body.append(el("h5", {}, `Track current (${r.source === "simulation" ? "from the simulation" : "estimated"})`),
        table(["Net", "Current", "Narrowest", `Heating (${m[0]})`, m[2], "Copper", "Drop"], r.tracks.map(t => [el("b", {}, t.net), `${t.current_ma} mA`,
          `${t.narrowest_mm} mm ${t.layer}`, `+${t.rise_c} °C`, t[`rise_${m[1]}_c`] != null ? `+${t[`rise_${m[1]}_c`]} °C` : "–",
          `${t.copper_mohm} mΩ`, `${t.drop_mv} mV`])));
    }
    if (r.decoupling.length) body.append(el("h5", {}, "Decoupling capacitors"),
      table(["IC pin", "Net", "Nearest cap", "Distance"], r.decoupling.map(d => [`${d.ref}.${d.pad} ${d.pin !== d.pad ? d.pin : ""}`, d.net,
        d.cap || "none", d.distance_mm != null ? `${d.distance_mm} mm` : "–"])));
    return card(v.title, body);
  },
  review(v) {
    const body = el("div", {}, el("p", {}, v.summary || ""));
    const probs = (v.issues || []).filter(i => i.severity === "problem"), notes = (v.issues || []).filter(i => i.severity !== "problem");
    if (probs.length) body.append(el("ul", { class: "list-err" }, probs.map(i => el("li", {}, i.text))));
    if (notes.length) body.append(el("ul", { class: "list-warn" }, notes.map(i => el("li", {}, i.text))));
    if (!probs.length && !notes.length) body.append(el("p", { class: "muted" }, "Nothing to report."));
    return card(v.title, body);
  },
};

function table(head, rows) {
  return el("table", {}, el("thead", {}, el("tr", {}, head.map(h => el("th", {}, h)))),
    el("tbody", {}, rows.map(r => el("tr", {}, r.map(c => el("td", {}, c ?? "–"))))));
}

const n3 = v => v == null ? "–" : (Math.abs(v) >= 100 ? Math.round(v) : +Number(v).toPrecision(3));

function renderSim(v) {
  const r = v.result, body = el("div");
  const stat = (val, label, cls) => el("div", { class: "stat " + (cls || "") }, el("div", { class: "v" }, val), el("div", { class: "l" }, label));
  if (!r.ran) {
    body.append(el("p", {}, el("b", {}, r.skipped ? `Not simulated: ${r.skipped}.` : "Not simulated.")));
    if (r.skipped === "ngspice not found") body.append(el("p", { class: "muted" }, "Install ngspice (Settings › Tools) to simulate boards."));
  } else {
    const sup = (r.supplies || []).filter(s => s.kind !== "signal");
    body.append(el("div", { class: "stats" },
      stat(r.problems.length, "problems", r.problems.length ? "bad" : "good"),
      stat(r.warnings.length, "warnings"),
      ...sup.slice(0, 2).map(s => stat(`${n3(s.current_ma)} mA`, `from ${s.net} (${s.volts} V)`)),
      ...sup.filter(s => s.battery_hours).slice(0, 1).map(s => stat(s.battery_hours >= 48 ? `${n3(s.battery_hours / 24)} days` : `${n3(s.battery_hours)} h`, "battery life")),
      ...(r.signals || []).slice(0, 2).map(s => stat(`${n3(s.frequency_hz)} Hz`, `${s.net}, ${Math.round(s.duty * 100)}% high`))));
  }
  if (r.problems?.length) body.append(el("ul", { class: "list-err" }, r.problems.map(x => el("li", {}, x))));
  if (r.warnings?.length) body.append(el("ul", { class: "list-warn" }, r.warnings.map(x => el("li", {}, x))));
  if (r.expectations?.length) body.append(el("h5", {}, "What the design should do"),
    table(["", "Expected", "Simulated"], r.expectations.map(e => [el("span", { class: "tag " + (e.ok ? "basic" : "bad") }, e.ok ? "pass" : "fail"), e.text, e.measured])));
  const plotHost = el("div", { class: "plot-host" });
  body.append(plotHost);
  if (v.plot) fetch(v.plot).then(x => x.json()).then(d => {
    if (d.plot?.t?.length) {
      const volts = Object.entries(d.plot.series).filter(([k]) => k.endsWith(" V"));
      const amps = Object.entries(d.plot.series).filter(([k]) => k.endsWith(" mA"));
      if (volts.length) { const h = el("div"); plotHost.append(el("h5", {}, "Voltages"), h); new Plot(h, d.plot.t, volts.map(([name, values]) => ({ name: name.replace(/ V$/, ""), values })), { ylabel: "V", xlabel: "time" }); }
      if (amps.length) { const h = el("div"); plotHost.append(el("h5", {}, "Supply current"), h); new Plot(h, d.plot.t, amps.map(([name, values]) => ({ name: name.replace(/ mA$/, ""), values })), { ylabel: "mA", xlabel: "time", height: 180 }); }
    }
    if (d.ac?.f?.length && Object.keys(d.ac.db).length) {
      const h = el("div"); plotHost.append(el("h5", {}, "Frequency response"), h);
      new Plot(h, d.ac.f, Object.entries(d.ac.db).map(([name, values]) => ({ name, values })), { ylabel: "dB", xlabel: "Hz", logx: true });
    }
  });
  if (r.rails?.length) body.append(el("h5", {}, "Power rails"), table(["Net", "Voltage", "At peak load", "Ripple"],
    r.rails.map(x => [el("b", {}, x.net), `${n3(x.volts)} V`, `${n3(x.peak_load)} V`, `${n3(x.ripple_v * 1000)} mV`])));
  if (r.supplies?.length) body.append(el("h5", {}, "Supplies"), table(["Net", "Kind", "Current", "Peak", "Start-up", "Limit", "Battery"],
    r.supplies.map(s => [el("b", {}, s.net), s.kind, `${n3(s.current_ma)} mA`, `${n3(s.peak_ma)} mA`, `${n3(s.startup_peak_ma)} mA`,
      s.max_ma ? `${s.max_ma} mA` : "–", s.battery_hours ? `${n3(s.battery_hours)} h` : "–"])));
  if (r.signals?.length) body.append(el("h5", {}, "Signals that toggle"), table(["Net", "Frequency", "Duty", "Low → high"],
    r.signals.map(s => [el("b", {}, s.net), `${n3(s.frequency_hz)} Hz`, `${Math.round(s.duty * 100)}%`, `${n3(s.low_v)} → ${n3(s.high_v)} V`])));
  const parts = (r.parts || []).filter(x => x.kind !== "capacitor" || x.rating_v);
  if (parts.length) {
    const detail = x => {
      if (x.kind === "capacitor") return `${n3(x.volts)} V of ${x.rating_v} V`;
      if (x.kind === "led") return `${n3(x.current_ma)} mA of ${n3(x.max_ma)} mA, Vf ${x.vf} V`;
      if (x.kind === "ldo" || x.kind === "converter") return `${n3(x.vin)} → ${n3(x.vout)} V, ${n3(x.current_ma)} mA (peak ${n3(x.peak_ma)} of ${n3(x.max_ma)})` + (x.temp_c ? `, ${n3(x.temp_c)} °C` : "");
      if (x.kind === "resistor") return `${n3(x.current_ma)} mA, ${n3(x.power_mw)} of ${n3(x.rating_mw)} mW`;
      if (x.kind === "ic") return `${n3(x.current_ma)} mA (peak ${n3(x.peak_ma)})` + (x.supply_v != null ? ` at ${n3(x.supply_v)} V` : "");
      return [x.current_ma != null ? `${n3(x.current_ma)} mA` : "", x.power_mw != null ? `${n3(x.power_mw)} mW` : "",
              x.temp_c != null ? `${n3(x.temp_c)} °C` : "", x.supply_v != null ? `${n3(x.supply_v)} V supply` : ""].filter(Boolean).join(", ");
    };
    body.append(el("details", {}, el("summary", {}, `Every part (${parts.length})`),
      table(["Ref", "Value", "Model", "Simulated"], parts.map(x => [el("b", {}, x.ref), x.value || "", x.kind, detail(x)]))));
  }
  const notes = [...(r.notes || []), ...(r.assumptions || [])];
  if (notes.length) body.append(el("details", {}, el("summary", {}, `Assumptions the models made (${notes.length})`),
    el("ul", { class: "assume" }, notes.map(a => el("li", {}, a)))));
  return card(v.title, body, { right: v.netlist ? el("a", { href: v.netlist, target: "_blank" }, "SPICE netlist") : "" });
}

function renderPower(v) {
  const b = v.budget, body = el("div");
  if (b.notes?.length) body.append(el("ul", { class: "list-warn" }, b.notes.map(x => el("li", {}, x))));
  const bar = pct => pct == null ? "–" : el("span", { class: "meter" + (pct > 90 ? " bad" : pct > 75 ? " warn" : "") },
    el("i", { style: `width:${Math.min(pct, 100)}%` }), el("b", {}, `${Math.round(pct)} %`));
  if (b.supplies.length) body.append(el("h5", {}, "Supplies"), table(["Input", "Typical", "Peak", "Can supply", "Used at peak"],
    b.supplies.map(s => [el("b", {}, s.net), `${n3(s.current_ma)} mA`, `${n3(s.peak_ma)} mA`, s.max_ma ? `${s.max_ma} mA` : "–", bar(s.use_pct)])));
  if (b.regulators.length) body.append(el("h5", {}, "Regulators"), table(["Part", "In → out", "Load (typ / peak)", "Rating", "Used", "Heat", "Junction"],
    b.regulators.map(r => [el("b", {}, `${r.ref} ${r.value || ""}`), `${n3(r.vin)} → ${n3(r.vout)} V`, `${n3(r.current_ma)} / ${n3(r.peak_ma)} mA`,
      r.max_ma ? `${r.max_ma} mA` : "–", bar(r.use_pct), r.power_mw != null ? `${n3(r.power_mw)} mW` : "–", r.temp_c != null ? `${Math.round(r.temp_c)} °C` : "–"])));
  for (const r of b.rails) body.append(el("h5", {}, el("span", {}, `${r.net} loads`), el("span", { class: "muted" }, `${n3(r.typ_ma)} mA typical, ${n3(r.peak_ma)} mA peak`)),
    table(["Part", "Typical", "Peak"], r.loads.map(l => [l.ref, `${n3(l.typ_ma)} mA`, `${n3(l.peak_ma)} mA`])));
  for (const bt of b.battery) body.append(el("h5", {}, `Battery on ${bt.net} (${bt.capacity_mah} mAh)`),
    el("p", {}, `${bt.hours_typical ?? "–"} h at the typical draw, ${bt.hours_peak ?? "–"} h flat out` +
      (bt.profile ? `; ${bt.profile.hours} h if active ${bt.profile.active_pct}% of the time and drawing ${bt.profile.sleep_ma} mA asleep.` : ". Add simulation.battery_profile if it sleeps.")));
  if (b.thermal.length) body.append(el("h5", {}, "Hottest parts"), table(["Part", "Dissipates", "Temperature", "Margin"],
    b.thermal.map(t => [`${t.ref} ${t.value || ""}`, `${n3(t.power_mw)} mW`, `${Math.round(t.temp_c)} °C`,
      el("span", { class: t.margin_c < 20 ? "bad-text" : "" }, `${Math.round(t.margin_c)} °C below ${t.limit_c} °C`)])));
  return card(v.title, body);
}

// ------------------------------------------------------------------ Sim Lab
function slValue(p, v) {
  if (p.unit === "Ω") return slOhms(v);
  if (p.marks) return `${Math.round(v)} W/m²K, ` + p.marks.reduce((a, m) => Math.abs(m[0] - v) < Math.abs(a[0] - v) ? m : a)[1];
  if (p.unit === "V") return `${v.toFixed(2)} V`;
  if (p.unit === "mA") return `${n3(v)} mA`;
  if (p.unit === "oz") return `${v} oz`;
  return `${Math.round(v)} ${p.unit}`;
}

function renderSimLab() {
  const host = el("div", { class: "simlab-stage" });
  const side = el("div", { class: "simlab-side" });
  const tourBtn = el("button", { class: "btn", type: "button", disabled: true }, "▶ Play tour");
  const recBtn = el("button", { class: "btn primary", type: "button", disabled: true }, "● Record video");
  const c = card("Sim Lab: the simulation on the 3D board", el("div", { class: "simlab" }, host, side),
    { fill: true, right: el("span", { class: "simlab-actions" }, tourBtn, recBtn) });
  c.classList.add("simlab-card");
  side.append(el("p", { class: "muted" }, "Loading the board and its simulation…"));
  const key = `${S.pid}:${S.project.revision}`;
  const st = S.simlab?.key === key ? S.simlab : (S.simlab = { key, values: {}, scene: null });
  (st.scene ? Promise.resolve(st.scene) : api(`/api/projects/${S.pid}/simlab`)).then(scene => {
    st.scene = scene;
    if (!host.isConnected && !document.contains(c)) return;
    buildSimLab(scene, st, host, side, tourBtn, recBtn);
  }).catch(e => { side.innerHTML = ""; side.append(el("p", { class: "err" }, e.message)); });
  return c;
}

function buildSimLab(scene, st, host, side, tourBtn, recBtn) {
  const lab = new SimLab(host, scene, { onSelect: ref => { selRisk = null; selPart = ref; drawSide(); } });
  if (!lab.gl) return;
  let dyn = st.dyn || scene, selRisk = null, selPart = null, timer = null, busy = 0, touring = false, redraw = false;
  const params = Object.fromEntries(scene.params.map(p => [p.id, p]));
  const value = id => st.values[id] ?? params[id].value;
  const status = el("p", { class: "simlab-status muted" }, "Click a risk or a part to see what changes it.");
  const toggles = el("div", { class: "simlab-toggles" });
  const chips = {};
  const syncChips = () => { for (const [k, b] of Object.entries(chips)) b.classList.toggle("on", !!lab.show[k]); };
  for (const [k, label] of [["flow", "Current flow"], ["heat", "Heat map"], ["glow", "LED glow"], ["labels", "Labels"], ["box", "Enclosure"]]) {
    if (k === "box" && !scene.box) continue;
    const b = chips[k] = el("button", { type: "button", class: "chip toggle" + (lab.show[k] ? " on" : "") }, label);
    b.onclick = () => { lab.show[k] = !lab.show[k]; syncChips(); lab.texDirty = true; };
    toggles.append(b);
  }
  toggles.append(el("button", { type: "button", class: "chip", onclick: () => { lab.focusRefs = []; lab.sel = null; selRisk = selPart = null; lab.home(); drawSide(); } }, "Reset view"));
  if (st.dyn) lab.update(st.dyn);
  for (const id of ["airflow", "copper", "ambient"]) if (st.values[id] != null) lab.setLocal(id, st.values[id]);

  const choice = p => {
    const sel = el("select", {}, p.options.map(([v, label]) => el("option", { value: v }, label)));
    sel.value = value(p.id);
    sel.onchange = () => {
      st.values[p.id] = p.id === "box" ? +sel.value : sel.value;
      if (p.id === "box") { lab.show.box = !!+sel.value; if (lab.show.box) { lab.show.heat = true; lab.show.flow = false; } syncChips(); }
      redraw = true;      // the enclosure's controls and note change with it
      resim();
    };
    return el("label", { class: "simlab-slider simlab-choice" + (value(p.id) !== p.value ? " changed" : "") }, el("span", {}, p.label), sel);
  };
  const slider = p => {
    if (p.kind === "choice") return choice(p);
    const log = !!p.log, toPos = v => log ? Math.log(v / p.min) / Math.log(p.max / p.min) * 1000 : v;
    const fromPos = x => log ? p.min * (p.max / p.min) ** (x / 1000) : +x;
    const out = el("b", {}, slValue(p, value(p.id)));
    const changed = value(p.id) !== p.value;
    const inp = el("input", { type: "range", min: log ? 0 : p.min, max: log ? 1000 : p.max, step: log ? 1 : p.step, value: toPos(value(p.id)) });
    inp.addEventListener("input", () => {
      let v = fromPos(inp.value);
      if (log) v = +v.toPrecision(2);
      st.values[p.id] = v; out.textContent = slValue(p, v);
      if (p.local || p.id === "ambient") lab.setLocal(p.id, v);
      if (!p.local) resim();
    });
    return el("label", { class: "simlab-slider" + (changed ? " changed" : "") }, el("span", {}, p.label), out, inp);
  };
  const resim = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const values = Object.fromEntries(Object.entries(st.values).filter(([id]) => !params[id]?.local));
      const t0 = performance.now(), mine = ++busy;
      status.textContent = "Re-running the simulation…";
      try {
        const d = await api(`/api/projects/${S.pid}/simlab/whatif`, { method: "POST", body: { values } });
        if (mine !== busy) return;
        dyn = st.dyn = d; lab.update(d); syncChips();
        const probs = d.risks.filter(r => r.level === "problem").length;
        status.textContent = `Re-simulated in ${((performance.now() - t0) / 1000).toFixed(1)} s: ` + (probs ? `${probs} problem${probs > 1 ? "s" : ""}` : "no problems") + ".";
        if (redraw) { redraw = false; drawSide(); } else { drawRisks(); if (boxNote) boxNote.textContent = boxNoteText(); }
      } catch (e) { status.textContent = "Simulation failed: " + e.message; }
    }, 350);
  };
  const riskList = el("ul", { class: "simlab-risks" }), adjust = el("div");
  const drawRisks = () => {
    riskList.innerHTML = "";
    if (!dyn.risks.length) riskList.append(el("li", { class: "muted" }, "Nothing to worry about. Try the sliders."));
    for (const r of dyn.risks) {
      const li = el("li", { class: `risk ${r.level}` + (selRisk === r.text ? " sel" : "") }, el("span", { class: "tag" }, r.level === "watch" ? "watch" : r.level), el("span", {}, r.text));
      li.onclick = () => {
        selRisk = r.text; selPart = null; lab.sel = null;
        lab.focusRefs = r.refs;
        if (/°C|heat|hot|dissipat/i.test(r.text)) { lab.show.heat = true; lab.show.flow = false; }
        if (r.source === "Enclosure" && dyn.box_heat?.on) { lab.show.box = true; syncChips(); }
        r.refs.length ? lab.focus(r.refs) : lab.home();
        drawSide();
      };
      riskList.append(li);
    }
  };
  let boxNote = null;
  const boxNoteText = () => {
    const bx = dyn.box_heat;
    if (!bx) return "";
    return bx.on ? `Inside air ${bx.air_c.toFixed(1)} °C. With the cooler walls round it the board sees ${bx.board_env_c.toFixed(1)} °C, and the circuit is simulated at that.`
      + (scene.box.kind === "suggested" ? " No enclosure is designed yet, so this is the box WholeBoard would suggest." : "")
      : `In ${scene.box.name} the air inside would reach ${bx.air_c.toFixed(0)} °C.`;
  };
  const drawAdjust = () => {
    adjust.innerHTML = "";
    let ids = [], title = "";
    const r = selRisk && dyn.risks.find(x => x.text === selRisk);
    if (r) { ids = r.params; title = "What changes this"; }
    else if (selPart) { ids = (dyn.links || scene.links || {})[selPart] || []; title = `${selPart}: ` + lab.describe(selPart).slice(1).join(" · "); }
    const boxOn = !!dyn.box_heat?.on, boxIds = ["box", "vents", "material", "placement"];
    // inside the box the board's cooling comes from the box, not the cooling slider
    const board = ["ambient", ...(boxOn ? [] : ["airflow"]), "copper", ...scene.params.filter(p => p.id.startsWith("src:")).map(p => p.id)];
    ids = ids.filter(id => params[id] && !(boxOn && id === "airflow") && !(!boxOn && boxIds.slice(1).includes(id)));
    if (title) adjust.append(el("h5", {}, title), ids.length ? el("div", {}, ids.map(id => slider(params[id])))
      : el("p", { class: "muted" }, "No slider changes this part directly; try the supply and air below."));
    if (scene.box) {
      const shown = (boxOn ? boxIds : ["box"]).filter(id => !ids.includes(id));
      boxNote = el("p", { class: "muted simlab-note" }, boxNoteText());
      if (shown.length) adjust.append(el("h5", {}, "Enclosure"), el("div", {}, shown.map(id => slider(params[id]))), boxNote);
    }
    adjust.append(el("h5", {}, boxOn ? "Board and room" : "Board and air"), el("div", {}, board.filter(id => !ids.includes(id) && params[id]).map(id => slider(params[id]))));
    const others = scene.params.filter(p => !board.includes(p.id) && !ids.includes(p.id) && !boxIds.includes(p.id) && p.id !== "airflow");
    if (others.length) adjust.append(el("details", {}, el("summary", {}, `Every part value (${others.length})`), others.map(slider)));
    if (Object.keys(st.values).length) adjust.append(el("button", { class: "btn", type: "button", onclick: () => {
      st.values = {}; st.dyn = null; dyn = scene; lab.update(scene); syncChips();
      for (const id of ["airflow", "copper", "ambient"]) lab.setLocal(id, params[id].value);
      status.textContent = "Back to the design's own values."; drawSide();
    } }, "Reset all values"));
  };
  const videos = el("div", { class: "simlab-videos" });
  const drawSide = () => { drawRisks(); drawAdjust(); };
  side.innerHTML = "";
  side.append(toggles, status, el("h5", {}, "Risks and things to watch"), riskList, adjust, videos);
  drawSide();

  const whatif = values => api(`/api/projects/${S.pid}/simlab/whatif`, { method: "POST", body: { values: { ...st.values, ...values } } });
  const runTour = async record => {
    if (touring) { touring = false; return; }
    touring = true;
    tourBtn.textContent = "■ Stop"; recBtn.disabled = tourBtn.disabled = false;
    if (record) { recBtn.disabled = true; tourBtn.disabled = true; }
    const saved = { ...lab.show };
    try {
      if (record) lab.startRecording();
      await slTour(lab, { whatif, stopped: () => !touring || !host.isConnected });
      if (record) {
        const blob = await lab.stopRecording();
        status.textContent = "Saving the video…";
        const local = URL.createObjectURL(blob), ext = blob.type.includes("mp4") ? "mp4" : "webm";
        videos.innerHTML = "";
        videos.append(el("h5", {}, "Your video"), el("video", { src: local, controls: true, class: "simlab-video" }));
        try {
          const r = await fetch(`/api/projects/${S.pid}/simlab/video`, { method: "POST", headers: { "Content-Type": blob.type }, body: blob }).then(x => x.json());
          videos.append(el("div", { class: "files" }, (r.files || []).map(f => el("a", { class: "btn", href: f.url, download: f.name }, "⬇ " + f.name))));
          status.textContent = "Video saved with the project's files.";
        } catch (e) {
          videos.append(el("a", { class: "btn", href: local, download: `simlab.${ext}` }, "⬇ Download video"));
          status.textContent = "Could not save it on the server; download it here.";
        }
      }
    } catch (e) { status.textContent = e.message; if (lab.rec) await lab.stopRecording(); }
    lab.show = saved; lab.texDirty = true; touring = false;
    tourBtn.textContent = "▶ Play tour"; tourBtn.disabled = recBtn.disabled = false;
  };
  tourBtn.disabled = recBtn.disabled = false;
  tourBtn.onclick = () => runTour(false);
  recBtn.onclick = () => runTour(true);
  if (S.simlabVideo) { S.simlabVideo = false; setTimeout(() => runTour(true), 600); }
}

function firmwareBuild(v, f) {
  const b = v.build || {}, box = el("div", { class: "fw-build" });
  const build = el("button", { class: "btn" + (b.status ? "" : " primary"), type: "button" }, b.status === "running" ? "Compiling…" : b.status ? "Compile again" : "Compile with PlatformIO");
  build.disabled = b.status === "running";
  build.onclick = async () => {
    build.disabled = true;
    try { await api(`/api/projects/${S.pid}/firmware/build`, { method: "POST" }); }
    catch (e) {
      if (/not installed/i.test(e.message) && confirm("PlatformIO is not installed. Install it now (pip, about 1 minute)?")) {
        await api("/api/tools/platformio/install", { method: "POST" }); await api(`/api/projects/${S.pid}/firmware/build`, { method: "POST" });
      } else alert(e.message);
    }
  };
  box.append(el("div", { class: "row-actions" }, build,
    el("span", { class: "muted" }, b.status === "running" ? "The first build of a chip family downloads its compiler; follow it in the log." :
      b.status === "done" ? `Compiled in ${b.seconds} s. ${(b.usage || []).join(" · ")}` : b.status === "failed" ? "It did not compile:" : "Proves the starter firmware compiles, then flash it from here.")));
  if (b.status === "failed" && b.error) box.append(el("pre", { class: "log-pre" }, b.error));
  if (b.status === "done") {
    if (b.kind === "esp") box.append(flashEsp(b));
    else if (b.files?.length) box.append(el("p", {}, ...b.files.map(x => el("a", { class: "btn", href: x.url, download: x.name }, "⬇ " + x.name)), " ",
      el("span", { class: "muted" }, b.kind === "uf2" ? "Hold BOOTSEL while plugging in USB, then drop firmware.uf2 on the drive that appears." :
        b.kind === "hex" ? "Flash it with avrdude or your ISP programmer (pio run -t upload does this)." : "Flash it with an ST-Link or pio run -t upload.")));
  }
  return box;
}

function flashEsp(b) {
  const out = el("div", { class: "flash" }), log = el("pre", { class: "log-pre", hidden: true });
  const btn = el("button", { class: "btn primary", type: "button" }, `Flash over USB (${b.chip})`);
  const why = !("serial" in navigator) ? "Your browser has no Web Serial: use Chrome, Edge or Opera on a desktop." :
    !window.isSecureContext ? `Web Serial needs a secure page: open WholeBoard as http://localhost:${location.port || 80} on the computer the board is plugged into.` : "";
  btn.disabled = !!why;
  btn.onclick = async () => {
    log.hidden = false; log.textContent = "";
    const say = t => { log.textContent += t + "\n"; log.scrollTop = log.scrollHeight; };
    let transport;
    try {
      const { ESPLoader, Transport } = await import("https://unpkg.com/esptool-js@0.5.7/bundle.js");
      const port = await navigator.serial.requestPort();
      transport = new Transport(port, true);
      const term = { clean() {}, writeLine: say, write: t => { log.textContent += t; } };
      const loader = new ESPLoader({ transport, baudrate: 460800, romBaudrate: 115200, terminal: term });
      say("Connecting… (if it hangs, hold BOOT and tap RESET)");
      say("Chip: " + await loader.main());
      const files = [];
      for (const f of b.files) {
        const buf = new Uint8Array(await (await fetch(f.url)).arrayBuffer());
        let s = ""; for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
        files.push({ data: s, address: f.offset });
      }
      await loader.writeFlash({ fileArray: files, flashSize: "keep", flashMode: "keep", flashFreq: "keep", eraseAll: false, compress: true,
        reportProgress: (i, done, total) => { btn.textContent = `Flashing ${b.files[i].name} ${Math.round(100 * done / total)} %`; } });
      await loader.after("hard_reset");
      say("Done: the board has restarted with the new firmware.");
    } catch (e) { say("Failed: " + (e.message || e)); }
    finally { try { await transport?.disconnect(); } catch (e) { /* already closed */ } btn.textContent = `Flash over USB (${b.chip})`; }
  };
  out.append(el("div", { class: "row-actions" }, btn, el("span", { class: "muted" }, why || "Plug the board in by USB, click, and pick its serial port."),
    ...b.files.map(x => el("a", { href: x.url, download: x.name, class: "muted", title: `at 0x${x.offset.toString(16)}` }, x.name))), log);
  return out;
}

function renderStock(q) {
  const st = S.project.stock, box = el("div", { class: "stock" });
  const btn = el("button", { class: "btn primary", type: "button" }, st ? "Check again" : "Check stock now");
  btn.onclick = async () => { btn.disabled = true; btn.textContent = "Checking…"; try { await api(`/api/projects/${S.pid}/stock`, { method: "POST" }); } catch (e) { alert(e.message); btn.disabled = false; } };
  box.append(el("h5", {}, el("span", {}, "Before you order: live stock"), el("span", { class: "muted" }, st ? `checked ${st.checked_at}` : "")),
    el("div", { class: "row-actions" }, btn, el("span", { class: "muted" }, "Stock changes daily: check right before ordering.")));
  if (st) {
    const bad = st.lines.filter(l => l.status !== "ok");
    box.append(el("p", { class: st.ok ? "good-text" : "bad-text" }, st.ok ? `All ${st.lines.length} lines in stock for ${st.boards} boards` + (st.low ? `; ${st.low} running low.` : ".") :
      `${st.problems} line${st.problems > 1 ? "s" : ""} cannot be supplied for ${st.boards} boards.`));
    if (bad.length) box.append(table(["Refs", "Part", "Status", "Stock", "Need", "Note"], bad.map(l => [l.refs.join(", "), l.lcsc || "–",
      el("span", { class: "tag " + (["short", "unavailable", "not found"].includes(l.status) ? "bad" : "extended") }, l.status), l.stock?.toLocaleString?.() ?? "–", l.order_qty ?? l.need, l.note || ""])));
    if (st.replacements?.length) box.append(el("p", {}, "In-stock replacements: ", ...st.replacements.map(x => el("span", { class: "chip" }, `${x.refs.join(", ")} → ${x.to.lcsc} ${x.to.mpn || ""}`))),
      el("p", { class: "muted" }, "Apply them from the Cheaper parts list above, or ask the designer."));
  }
  return box;
}

function renderFabs(q) {
  if (!q.fabs?.length) return "";
  return el("details", {}, el("summary", {}, "Other fabs and assemblers"),
    table(["Fab", "Boards", "Price", "Assembly", "Shipping", "Lead time", "Notes"], q.fabs.map(x => [el("a", { href: x.url, target: "_blank" }, x.fab), x.boards ?? "–",
      x.total != null ? money(x.total) + (x.kind === "indicative" ? " (indicative)" : "") : (x.kind === "quote" ? "–" : "quote online"),
      x.assembly_total != null ? money(x.assembly_total) : (x.kind === "quote" ? "quote online" : "–"), x.shipping || "–", x.lead_time || "–",
      el("span", { class: "muted" }, (x.basis ? x.basis + ". " : "") + (x.assembly_basis ? "Assembly: " + x.assembly_basis + ". " : "") + (x.notes || ""))])));
}

function renderQuote(v) {
  const q = v.quote, o = v.options || {};
  const sec = (title, part) => part ? [el("h5", {}, el("span", {}, title), el("span", {}, money(part.subtotal))),
    el("table", {}, el("tbody", {}, part.lines.map(l => el("tr", {}, el("td", {}, l.item, l.note ? el("div", { class: "muted" }, l.note) : ""), el("td", { class: "num" }, money(l.amount))))))] : [];
  const left = el("div", { class: "quote" });
  left.append(...sec("PCB fabrication", q.pcb));
  if (q.assembly) left.append(...sec(`Assembly (${q.assembly.type}, ${q.assembly.boards} boards)`, q.assembly));
  if (q.components) {
    left.append(el("h5", {}, el("span", {}, "Components"), el("span", {}, money(q.components.subtotal))),
      el("table", {}, el("thead", {}, el("tr", {}, ["Refs", "Part", "Type", "Stock", "Qty", "Unit", "Total"].map((h, i) => el("th", { class: i > 2 ? "num" : "" }, h)))),
        el("tbody", {}, q.components.lines.map(l => el("tr", {},
          el("td", {}, l.refs.join(", ")),
          el("td", {}, l.lcsc ? el("a", { href: `https://jlcpcb.com/partdetail/${l.lcsc}`, target: "_blank" }, l.lcsc) : "–", " ", el("span", { class: "muted" }, l.mpn || l.value || "")),
          el("td", {}, l.library_type ? el("span", { class: "tag " + l.library_type }, l.library_type) : "", l.status !== "ok" ? el("span", { class: "tag bad" }, l.status) : ""),
          el("td", { class: "num" }, l.stock?.toLocaleString?.() ?? "–"), el("td", { class: "num" }, l.order_qty),
          el("td", { class: "num" }, l.unit != null ? "$" + l.unit.toFixed(4) : "–"), el("td", { class: "num" }, money(l.total)))))));
  }
  const su = q.suppliers;
  if (su?.parts?.length) {
    left.append(el("h5", {}, el("span", {}, "Parts JLCPCB cannot fit"), el("span", {}, su.total != null ? money(su.total) : "")),
      table(["Refs", "Part", "Why", "Best offer", "Stock", "Unit", "Total"], su.parts.map(r => {
        const o = r.offers?.[0];
        const look = r.links ? el("span", { class: "muted" }, " ", el("a", { href: r.links.mouser, target: "_blank" }, "Mouser"), " · ",
          el("a", { href: r.links.digikey, target: "_blank" }, "DigiKey")) : "";
        return [r.refs.join(", "), el("span", {}, r.mpn || r.value || "–", look), r.why,
          o ? el("a", { href: o.url || "#", target: "_blank" }, `${o.supplier} ${o.sku || ""}`) : (r.errors?.[0] || (su.keys ? "not found" : "add API keys in Settings")),
          o ? o.stock.toLocaleString() : "–", o?.unit != null ? "$" + o.unit.toFixed(4) : "–", o ? money(o.total) : "–"];
      })),
      el("p", { class: "muted" }, "Order these and fit them by hand, or send them to JLCPCB as consigned parts. " +
        (su.keys ? "Prices from the suppliers' APIs." : "Add Mouser or DigiKey API keys in Settings to see live prices.")));
  }
  const sv = q.savings;
  if (sv?.suggestions?.length) {
    const boxes = sv.suggestions.map(() => el("input", { type: "checkbox", checked: true }));
    const btn = el("button", { class: "btn primary", type: "button" }, "Use the ticked parts");
    btn.addEventListener("click", async () => {
      const swaps = sv.suggestions.filter((_, i) => boxes[i].checked).map(x => ({ refs: x.refs, lcsc: x.to.lcsc }));
      if (!swaps.length) return;
      btn.disabled = true;
      try { await api(`/api/projects/${S.pid}/swaps`, { method: "POST", body: { swaps } }); } finally { btn.disabled = false; }
    });
    left.append(el("h5", {}, el("span", {}, "Cheaper parts"), el("span", {}, sv.total_saving ? "save " + money(sv.total_saving) : "")),
      table(["", "Refs", "Now", "Use instead", "Why", "Saves"], sv.suggestions.map((x, i) => [boxes[i], x.refs.join(", "),
        el("span", {}, x.from.lcsc || "–", " ", x.from.library_type ? el("span", { class: "tag " + x.from.library_type }, x.from.library_type) : ""),
        el("span", {}, el("a", { href: `https://jlcpcb.com/partdetail/${x.to.lcsc}`, target: "_blank" }, x.to.lcsc), " ",
          el("span", { class: "tag " + x.to.library_type }, x.to.library_type), el("div", { class: "muted" }, x.to.mpn || "")),
        x.reason, x.saving ? money(x.saving) : "–"])),
      el("div", { class: "row-actions" }, btn,
        el("span", { class: "muted" }, " Same value and package, or the same part number. The BOM and CPL files are rewritten; the board is unchanged.")));
  }
  const po = q.panel_options;
  if (po?.layouts?.length && !q.panel) {
    const r = po.recommended;
    left.append(el("h5", {}, el("span", {}, "Panel"), el("span", {}, r ? `${r.nx} × ${r.ny} suggested` : "")),
      r ? el("p", {}, po.reason, " ", el("span", { class: "muted" }, po.how)) : el("p", { class: "muted" }, "Single boards are the cheapest way to make these. " + po.how),
      el("details", {}, el("summary", {}, "Panel layouts compared"),
        table(["Layout", "Panel size", "Separation", "Panels", "Boards", "PCB cost", "Per board"],
          [[`single boards`, "", "", "", po.single.boards, money(po.single.pcb_cost), "$" + po.single.per_board.toFixed(3)],
           ...po.layouts.map(l => [`${l.nx} × ${l.ny}`, `${l.width_mm} × ${l.height_mm} mm`, l.method, l.panels, l.boards, money(l.pcb_cost), "$" + l.per_board.toFixed(3)])])));
  }
  const want = q.panel || po?.recommended;
  if (want) {
    const pb = el("button", { class: "btn", type: "button" }, `Make the ${want.nx} × ${want.ny} panel files`);
    pb.onclick = async () => {
      pb.disabled = true; pb.textContent = "KiKit is drawing the panel…";
      try {
        const r = await api(`/api/projects/${S.pid}/panel`, { method: "POST", body: { nx: want.nx, ny: want.ny } });
        if (r.ok) selectStep("outputs", true); else alert(r.errors.join("\n"));
      } finally { pb.disabled = false; pb.textContent = `Make the ${want.nx} × ${want.ny} panel files`; }
    };
    left.append(el("div", { class: "row-actions" }, pb, el("span", { class: "muted" },
      " Rails, tooling holes, fiducials, panel Gerbers, BOM and CPL, drawn with KiKit. Shown under Outputs.")));
  }
  left.append(...sec("Shipping", q.shipping));
  left.append(el("div", { class: "quote-total" }, el("span", {}, "Total"), el("span", {}, money(q.total))),
    el("div", { class: "muted" }, `${money(q.per_board)} per ${q.assembly ? "assembled" : ""} board`),
    el("ul", { class: "assume" }, q.assumptions.map(a => el("li", {}, a))), renderFabs(q), renderStock(q));

  const f = el("form", { class: "quote-form" });
  const field = (label, input) => el("label", {}, label, input);
  const num = (name, val) => el("input", { name, type: "number", min: 1, value: val });
  const sel = (name, opts, val) => { const s = el("select", { name }, opts.map(x => el("option", { value: x }, x))); s.value = val; return s; };
  f.append(el("b", {}, "Quote options"),
    field("Boards wanted", num("quantity", o.quantity ?? q.quantity)),
    field("Panel (Panel by JLCPCB)", (() => {
      const choices = [["", "single boards"], ...(po?.layouts || []).map(l => [`${l.nx}x${l.ny}`, `${l.nx} × ${l.ny} (${l.method})`])];
      if (o.panel && !choices.some(c => c[0] === `${o.panel.nx}x${o.panel.ny}`)) choices.push([`${o.panel.nx}x${o.panel.ny}`, `${o.panel.nx} × ${o.panel.ny}`]);
      const s2 = el("select", { name: "panel" }, choices.map(([v, t]) => el("option", { value: v }, t)));
      s2.value = o.panel ? `${o.panel.nx}x${o.panel.ny}` : "";
      return s2;
    })()),
    field("Assembly", sel("assembly", ["yes", "no"], o.assembly === false ? "no" : "yes")),
    field("Boards assembled", num("assembly_quantity", o.assembly_quantity ?? 2)),
    field("Assembly type", sel("pcba_type", ["economic", "standard"], o.pcba_type || "economic")),
    field("Surface finish", sel("surface_finish", ["HASL(with lead)", "LeadFree HASL", "ENIG"], o.surface_finish || "HASL(with lead)")),
    field("Solder mask", sel("solder_mask", ["Green", "Red", "Yellow", "Blue", "White", "Black", "Purple"], o.solder_mask || "Green")),
    field("Shipping", sel("shipping_method", ["Global Standard Direct", "DHL Express", "FedEx International"], o.shipping_method || "Global Standard Direct")),
    el("button", { class: "btn primary", type: "submit" }, "Update quote"));
  f.addEventListener("submit", async ev => {
    ev.preventDefault();
    const d = Object.fromEntries(new FormData(f));
    d.quantity = +d.quantity; d.assembly_quantity = +d.assembly_quantity; d.assembly = d.assembly === "yes";
    d.panel = d.panel ? { nx: +d.panel.split("x")[0], ny: +d.panel.split("x")[1] } : null;
    d.shipping_country = o.shipping_country;
    f.querySelector("button").disabled = true;
    try { await api(`/api/projects/${S.pid}/quote`, { method: "POST", body: d }); } finally { f.querySelector("button").disabled = false; }
  });
  return card(v.title, el("div", { class: "quote-grid" }, left, f), { right: el("a", { href: q.order_url, target: "_blank" }, "Check on JLCPCB ↗") });
}

const fmtHours = h => { const m = Math.round(h * 60); return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`; };

function renderEnclosure(v) {
  const frag = document.createDocumentFragment();
  const s = v.summary;
  if (!s) return frag;
  const host = el("div", { class: "mesh-host" });
  const bar = el("div", { class: "board-bar" });
  const dl = el("span", { class: "dl" }, (v.files || []).map(f => el("a", { href: f.url, download: f.name, title: f.name }, "⬇ " + (f.label || f.name))));
  frag.append(card(v.title, el("div", { class: "mesh-wrap" }, host, bar), { fill: true, right: dl }));
  fetch(v.preview).then(r => r.json()).then(data => {
    const mv = new MeshView(host, data);
    for (const [key, label] of [["base", "Base"], ["lid", "Lid"], ["board", "Board"]]) {
      const b = el("button", { class: "chip toggle on", type: "button" }, label);
      b.addEventListener("click", () => { mv.show[key] = !mv.show[key]; b.classList.toggle("on", mv.show[key]); mv.draw(); });
      bar.append(b);
    }
    const rng = el("input", { type: "range", min: 0, max: 1.5, step: 0.05, value: mv.explode, title: "Lift the lid" });
    rng.addEventListener("input", () => { mv.explode = +rng.value; mv.draw(); });
    bar.append(el("label", { class: "chip" }, "Lid lift ", rng), el("span", { class: "chip" }, "drag to turn · wheel to zoom"));
  });
  const row = (k, val) => val || val === 0 ? el("tr", {}, el("td", {}, k), el("td", {}, val)) : null;
  const mm = a => a.map(x => (+x).toFixed(1)).join(" × ") + " mm";
  const info = el("div", { class: "enc-info" });
  if (s.collisions?.length) {
    info.append(el("div", { class: "enc-bad" }, el("b", {}, "Collisions found by FreeCAD"),
      el("ul", {}, s.collisions.map(c => el("li", {}, `${c.part} overlaps the ${c.with} by ${c.overlap_mm3} mm³`)))));
  }
  info.append(el("table", { class: "kv" }, el("tbody", {},
    row("Outside (with lid)", mm(s.outer_mm)), row("Inside", mm(s.inner_mm)),
    row("Board standoff", s.board_standoff_mm + " mm"), row("Board top to lid", s.board_top_to_lid_mm + " mm"),
    row("Tallest part", s.tallest_part), row("Holding the board", s.board_fixing), row("Lid", s.lid),
    row("Wall openings", s.openings?.length ? s.openings.map(x => `${x.for} (${x.wall} wall, ${x.width_mm} × ${x.height_mm} mm${x.shape === "round" ? ", round" : ""})`).join(", ") : "none"),
    row("Lid holes", s.lid_holes?.length ? s.lid_holes.map(x => `${x.for} ${x.size}`).join(", ") : "none"),
    row("Extras", s.extras?.length ? s.extras.join(", ") : null),
    row("Material", s.print ? `${s.print_volume_cm3} cm³, about ${s.print.grams} g of ${s.print.material}` : `${s.print_volume_cm3} cm³, about ${s.filament_g} g of PLA`),
    row("Printing", s.print ? `about ${fmtHours(s.print.hours)}, ${money(s.print.total)} (filament ${money(s.print.filament_cost)}, power ${money(s.print.power_cost)}`
      + (s.print.machine_cost ? `, machine ${money(s.print.machine_cost)}` : "") + ")" : null))));
  const notes = [...(s.notes || []), ...(s.freecad_notes || []), ...(s.warnings || [])];
  if (notes.length) info.append(el("ul", { class: "assume" }, notes.map(n => el("li", {}, n))));
  if (s.thermal) info.append(boxThermal(s.thermal));
  info.append(el("p", { class: "muted" }, "Print the base open side up and the lid flat side down, without supports. ",
    "PLA or PETG at 0.2 mm layers works well. Check the openings against your parts' datasheets before printing."));
  const side = el("div", { class: "quote-form" }, el("b", {}, "Change the enclosure"),
    el("p", { class: "muted" }, "Ask the designer in the chat, for example “screw the lid on”, “add vents” or “engrave BLINKER on the lid”."),
    el("button", { class: "btn", type: "button", onclick: () => openSpecDialog("enclosure", v.spec) }, "Edit enclosure JSON"),
    el("p", { class: "muted" }, "The FreeCAD model and macro open in FreeCAD for hand editing."));
  frag.append(card("Enclosure details", el("div", { class: "quote-grid" }, info, side)));
  return frag;
}

// how warm the board runs in its box (from boxheat, via the enclosure step)
function boxThermal(t) {
  const c = x => `${(+x).toFixed(0)} °C`;
  const box = el("div", { class: "box-heat" + (t.warnings.length ? " warm" : "") },
    el("h5", {}, "Heat in the box"),
    el("p", {}, `With the board's ${n3(t.power_mw)} mW inside, the air in the box settles at `, el("b", {}, c(t.air_c)),
      ` in a ${c(t.room_c)} room (${t.rise_k.toFixed(0)} °C warmer). The lid sits at ${c(t.surface_c.lid)}, the walls at ${c(t.surface_c.sides)}. `
      + `Vents: ${t.vents}; ${t.through_openings_pct}% of the heat leaves through openings, the rest through the ${t.material} walls.`
      + (t.with_vents_c < t.air_c - 1 ? ` Lid and side vents would bring the inside to ${c(t.with_vents_c)}.` : "")),
    t.parts.length ? table(["Part", "In the box", "Limit"], t.parts.slice(0, 4).map(x => [x.ref, c(x.temp_c), c(x.limit_c)])) : "",
    t.warnings.length ? el("ul", { class: "list-warn" }, t.warnings.map(w => el("li", {}, w))) : "",
    el("button", { class: "btn", type: "button", onclick: () => { S.follow = false; selectStep("sim", true); } }, "See it in the Sim Lab"));
  return box;
}

async function openHistory() {
  const list = $("#historyList"), cmp = $("#historyCompare"), btn = $("#historyCompareBtn");
  list.textContent = "Loading…"; cmp.innerHTML = ""; btn.disabled = true;
  $("#historyDialog").showModal();
  const data = await api(`/api/projects/${S.pid}/revisions`);
  const ticked = [];
  list.innerHTML = "";
  if (!data.revisions.length) { list.textContent = "No builds yet."; return; }
  for (const r of [...data.revisions].reverse()) {
    const box = el("input", { type: "checkbox" });
    box.addEventListener("change", () => {
      if (box.checked) { ticked.push(r.revision); if (ticked.length > 2) { const old = ticked.shift(); list.querySelector(`[data-rev="${old}"]`).checked = false; } }
      else ticked.splice(ticked.indexOf(r.revision), 1);
      btn.disabled = ticked.length !== 2;
    });
    box.dataset.rev = r.revision;
    const restore = el("button", { class: "btn small", type: "button" }, r.current ? "Rebuild" : "Restore");
    restore.addEventListener("click", async () => {
      if (!confirm(`Rebuild revision ${r.revision} as a new revision? Nothing is deleted.`)) return;
      $("#historyDialog").close();
      await api(`/api/projects/${S.pid}/revisions/${r.revision}/restore`, { method: "POST" });
    });
    const b = r.board || {};
    const status = r.ok === true ? el("span", { class: "tag ok" }, "built") : r.ok === false ? el("span", { class: "tag bad" }, r.stage || "failed") : "";
    list.append(el("div", { class: "rev" + (r.current ? " current" : "") },
      el("label", { class: "check" }, box),
      r.image_url ? el("img", { src: r.image_url, alt: "" }) : el("div", { class: "rev-noimg" }, "no render"),
      el("div", { class: "rev-main" },
        el("div", {}, el("b", {}, `Revision ${r.revision}`), r.current ? el("span", { class: "tag" }, "current") : "", status,
          r.edited_in_kicad ? el("span", { class: "tag warn", title: "Changed by hand in KiCad after it was built; the next revision kept the changes" }, "edited in KiCad") : "",
          r.kicad_edits ? el("span", { class: "tag", title: `Hand-placed: ${r.kicad_edits.hand_placed.join(", ") || "none"}. Hand-routed nets: ${r.kicad_edits.nets.join(", ") || "none"}.` },
            `keeps KiCad edits (${r.kicad_edits.hand_placed.length} parts, ${r.kicad_edits.nets.length} nets)`) : "",
          el("span", { class: "muted" }, " " + new Date(r.time * 1000).toLocaleString() + (r.by ? ` · ${r.by}` : ""))),
        el("div", { class: "muted" }, `${b.width_mm ?? "?"} × ${b.height_mm ?? "?"} mm, ${b.layers ?? "?"} layers, ${r.parts} parts, ${r.nets} nets` + (r.total != null ? ` · ${money(r.total)}` : "")),
        r.changes ? el("div", {}, r.changes) : "",
        r.errors?.length ? el("div", { class: "err" }, r.errors.join("; ")) : ""),
      restore));
  }
  btn.onclick = async () => {
    const [a, b2] = [...ticked].sort((x, y) => x - y);
    const d = await api(`/api/projects/${S.pid}/revisions/compare?a=${a}&b=${b2}`);
    const part = p => `${p.ref} ${p.value || ""} ${p.footprint ? "(" + p.footprint.split(":").pop() + ")" : ""}`;
    const fmt = v => v == null ? "–" : typeof v === "object" ? (v.x_mm != null ? `${v.x_mm}, ${v.y_mm} mm ${v.rotation || 0}° ${v.side || ""}` : JSON.stringify(v)) : String(v);
    const rows = [
      ...d.parts_added.map(p => ["Part added", part(p), ""]),
      ...d.parts_removed.map(p => ["Part removed", part(p), ""]),
      ...d.parts_changed.flatMap(c => Object.entries(c.changes).map(([k, [o, n]]) => [`${c.ref} ${k}`, fmt(o), fmt(n)])),
      ...d.nets_added.map(n => ["Net added", n.net, n.pins.join(" ")]),
      ...d.nets_removed.map(n => ["Net removed", n.net, n.pins.join(" ")]),
      ...d.nets_changed.map(n => [`Net ${n.net}`, n.removed.length ? "− " + n.removed.join(" ") : "", n.added.length ? "+ " + n.added.join(" ") : ""]),
      ...Object.entries(d.board).map(([k, [o, n]]) => [`Board ${k}`, fmt(o), fmt(n)]),
      ...Object.entries(d.rules).map(([k, [o, n]]) => [`Rule ${k}`, fmt(o), fmt(n)]),
    ];
    cmp.innerHTML = "";
    cmp.append(el("h5", {}, `Revision ${a} → revision ${b2}`),
      rows.length ? table(["What", `Rev ${a}`, `Rev ${b2}`], rows) : el("p", { class: "muted" }, "The two designs are the same."));
    cmp.scrollIntoView({ behavior: "smooth" });
  };
}

function openSpecDialog(mode, data) {
  S.specMode = mode;
  $("#specTitle").textContent = mode === "enclosure" ? "Enclosure JSON" : "Design JSON";
  $("#specHint").textContent = mode === "enclosure"
    ? "Edit the enclosure and rebuild it in FreeCAD without the AI. Walls are left, right, top and bottom as seen in the board view."
    : "Edit the design and rebuild it without the AI. The same checks run first.";
  $("#specText").value = JSON.stringify(data, null, 2);
  $("#specErr").textContent = "";
  $("#specDialog").showModal();
}

function zoomable(box, img) {
  let s = 1, x = 0, y = 0, drag = null;
  const apply = () => img.style.transform = `translate(${x}px, ${y}px) scale(${s})`;
  img.onload = () => {
    const w = box.clientWidth, h = box.clientHeight;
    s = Math.min(w / img.naturalWidth, h / img.naturalHeight) * 0.95 || 1;
    x = (w - img.naturalWidth * s) / 2; y = (h - img.naturalHeight * s) / 2; apply();
  };
  box.addEventListener("wheel", e => {
    e.preventDefault();
    const r = box.getBoundingClientRect(), z = wbZoom(), mx = (e.clientX - r.left) / z, my = (e.clientY - r.top) / z, k = Math.exp(-e.deltaY * 0.0015);
    x = mx - (mx - x) * k; y = my - (my - y) * k; s *= k; apply();
  }, { passive: false });
  box.addEventListener("pointerdown", e => { drag = [e.clientX, e.clientY]; box.setPointerCapture(e.pointerId); });
  box.addEventListener("pointerup", () => drag = null);
  box.addEventListener("pointermove", e => { if (!drag) return; const z = wbZoom(); x += (e.clientX - drag[0]) / z; y += (e.clientY - drag[1]) / z; drag = [e.clientX, e.clientY]; apply(); });
}

// ------------------------------------------------------------------ settings
async function openSettings() {
  const cfg = await api("/api/config");
  const form = $("#settingsForm");
  for (const input of form.querySelectorAll("[name]")) {
    const v = input.name.split(".").reduce((o, k) => o?.[k], cfg);
    if (input.type === "checkbox") input.checked = !!v; else input.value = v ?? "";
  }
  syncShow();
  $("#localModels").hidden = true; $("#findLocalOut").textContent = "";
  $("#settings").showModal();
}

function syncShow() {
  const form = $("#settingsForm");
  for (const box of form.querySelectorAll("[data-show]")) {
    const [name, val] = box.dataset.show.split("=");
    const ctl = form.querySelector(`[name="${name}"]`);
    box.hidden = (ctl.type === "checkbox" ? String(ctl.checked) : ctl.value) !== val;
  }
}

async function saveSettings() {
  const form = $("#settingsForm"), patch = {};
  for (const input of form.querySelectorAll("[name]")) {
    const keys = input.name.split(".");
    let v = input.type === "checkbox" ? input.checked : input.value;
    if (input.type === "number") v = Number(v);
    const leaf = keys.pop();
    keys.reduce((o, k) => (o[k] ||= {}), patch)[leaf] = v;
  }
  await api("/api/config", { method: "POST", body: patch });
  $("#settings").close();
  await loadStatus(true);
}

// Search the computer running WholeBoard (or the other computers in its subnet) for model servers
// and offer each chat model.
async function findLocalModels(lan = false) {
  const form = $("#settingsForm"), out = $("#findLocalOut"), box = $("#localModels");
  const btn = lan ? $("#findLanBtn") : $("#findLocalBtn"), both = [$("#findLocalBtn"), $("#findLanBtn")];
  const field = n => form.querySelector(`[name="ai.${n}"]`);
  out.textContent = "";
  both.forEach(b => b.disabled = true);
  btn.classList.add("busy"); btn.setAttribute("aria-busy", "true");
  btn.replaceChildren(el("span", { class: "spinner" }), "Searching…");
  box.hidden = false;
  box.replaceChildren(el("div", { class: "lm searching", role: "status" }, el("span", { class: "spinner" }), lan
    ? "Checking every address in this computer's subnet for model servers… this takes a few seconds."
    : "Looking for Ollama, LM Studio, llama.cpp, Strata, vLLM, Jan and other model servers on the computer running WholeBoard…"));
  let r;
  try {
    // keep the spinner up long enough to be seen when every server answers at once
    [r] = await Promise.all([lan ? api("/api/ai/lan-models", { method: "POST" })
                                 : api("/api/ai/local-models", { method: "POST", body: { url: field("openai_base_url").value } }),
                             new Promise(ok => setTimeout(ok, 500))]);
  } catch (e) { box.hidden = true; out.textContent = "✗ " + e.message; return; }
  finally {
    both.forEach(b => b.disabled = false);
    btn.classList.remove("busy"); btn.removeAttribute("aria-busy");
    btn.textContent = lan ? "Search the LAN again" : "Search again";
  }
  box.innerHTML = "";
  box.hidden = !r.models.length;
  const nets = (r.networks || []).map(n => n.whole ? `${n.network} (the part of ${n.whole} around this computer)` : n.network).join(", ");
  if (lan && !r.networks.length) { out.textContent = "This computer has no address on a LAN (10.x, 172.16–31.x or 192.168.x), so there is nothing to search."; return; }
  if (!r.models.length) {
    out.textContent = lan
      ? `No model servers answered on ${nets}. Ollama, LM Studio and llama.cpp only listen on their own computer `
        + "unless told to serve the network: set OLLAMA_HOST=0.0.0.0 for Ollama, turn on Serve on Local Network in "
        + "LM Studio, or start llama.cpp with --host 0.0.0.0."
      : "No local models found. Start Ollama, LM Studio or llama.cpp on the computer running WholeBoard, "
        + "or type its Server URL below, then search again.";
    return;
  }
  const servers = new Set(r.models.map(m => m.base_url)).size;
  out.textContent = `Found ${r.models.length} model${r.models.length === 1 ? "" : "s"} on ${servers} server${servers === 1 ? "" : "s"}`
    + (lan ? ` in ${nets}.` : ".");
  const current = field("provider").value === "openai" ? [field("openai_base_url").value, field("openai_model").value] : [];
  for (const m of r.models) {
    const tools = m.tools === true ? el("span", { class: "chip ok", title: "The model supports tool calling" }, "tools")
      : m.tools === false ? el("span", { class: "chip warn", title: "WholeBoard needs tool calling; this model may not design boards" }, "no tools") : "";
    const row = el("div", { class: "lm" + (current[0] === m.base_url && current[1] === m.model ? " on" : "") },
      el("div", { class: "n" }, el("b", { title: m.model }, m.model), el("span", {}, [m.server, m.detail].filter(Boolean).join(" · "))),
      tools,
      el("button", { class: "btn", type: "button", onclick: () => {
        field("provider").value = "openai";
        field("openai_base_url").value = m.base_url;
        field("openai_model").value = m.model;
        field("openai_price_in").value = 0; field("openai_price_out").value = 0;   // local models cost nothing per token
        const dl = $("#openaiModels"); dl.innerHTML = "";
        for (const x of r.models.filter(x => x.base_url === m.base_url)) dl.append(el("option", { value: x.model }));
        syncShow();
        box.querySelectorAll(".lm").forEach(x => x.classList.toggle("on", x === row));
        out.textContent = `Using ${m.model} on ${m.server}. Click Save, then Test connection.`;
      } }, "Use"));
    box.append(row);
  }
}

// ------------------------------------------------------------------ wiring
function init() {
  renderExamples();
  $("#homeLink").onclick = e => { e.preventDefault(); showHome(); };
  $("#newBtn").onclick = () => { showHome(); $("#prompt").focus(); };
  $("#settingsBtn").onclick = () => openSettings();
  $("#tourBtn").onclick = () => startTour();
  $("#saveSettings").onclick = () => saveSettings();
  $("#settingsForm").querySelector('[name="ai.provider"]').onchange = syncShow;
  document.querySelectorAll("#settingsTabs button").forEach(b => b.onclick = () => {
    document.querySelectorAll("#settingsTabs button").forEach(x => x.classList.toggle("on", x === b));
    document.querySelectorAll("#settingsForm .tab").forEach(t => t.hidden = t.dataset.tab !== b.dataset.tab);
  });
  $("#aiTestBtn").onclick = async () => {
    const out = $("#aiTestOut");
    out.textContent = "Saving and testing…";
    await saveSettings().catch(() => {});
    $("#settings").showModal();
    const r = await api("/api/ai/test", { method: "POST" });
    out.textContent = r.ok ? `✓ ${r.model} replied: ${r.reply}` : `✗ ${r.error}`;
  };
  document.querySelectorAll("[data-preset]").forEach(b => b.onclick = () => {
    const form = $("#settingsForm");
    form.querySelector('[name="ai.provider"]').value = "openai";
    form.querySelector('[name="ai.openai_base_url"]').value = b.dataset.preset;
    form.querySelector('[name="ai.openai_model"]').value = "";
    $("#openaiModels").innerHTML = "";
    syncShow();
    $("#modelsOut").textContent = b.dataset.preset.startsWith("https://")
      ? `Paste your ${b.textContent} API key, click Save, then List models.`
      : `Start ${b.textContent} on this computer, click Save, then List models.`;
  });
  $("#findLocalBtn").onclick = () => findLocalModels();
  $("#findLanBtn").onclick = () => findLocalModels(true);
  document.querySelectorAll("[data-list-models]").forEach(b => b.onclick = async () => {
    const out = $("#modelsOut");
    out.textContent = "Saving and asking the provider for its models…";
    b.disabled = true;
    try {
      await saveSettings().catch(() => {});
      $("#settings").showModal();
      const r = await api("/api/ai/models", { method: "POST" });
      if (!r.ok) { out.textContent = "✗ " + r.error; return; }
      const dl = document.getElementById(b.dataset.listModels);
      dl.innerHTML = "";
      for (const id of r.models) dl.append(el("option", { value: id }));
      out.textContent = `✓ ${r.models.length} models available. Click the Model box to pick one, then Save.`;
      const input = b.previousElementSibling;
      input.value = ""; input.focus();
    } finally { b.disabled = false; }
  });
  $("#freecadFetch").onclick = async () => {
    const out = $("#libOut"), b = $("#freecadFetch");
    out.textContent = "Downloading FreeCAD… this is about 800 MB and can take a while.";
    b.disabled = true;
    try {
      const r = await api("/api/tools/freecad/fetch", { method: "POST" });
      out.textContent = r.log.join("\n") + (r.ok ? `\nDone: ${r.freecad}` : "\nFailed.");
      loadStatus(true);
    } catch (e) { out.textContent = e.message; } finally { b.disabled = false; }
  };
  $("#ngspiceFetch").onclick = async () => {
    const out = $("#libOut"), b = $("#ngspiceFetch");
    out.textContent = "Installing ngspice…";
    b.disabled = true;
    try {
      const r = await api("/api/tools/ngspice/fetch", { method: "POST" });
      out.textContent = r.log.join("\n") + (r.ok ? "\nDone." : "");
      loadStatus(true);
    } catch (e) { out.textContent = e.message; } finally { b.disabled = false; }
  };
  document.querySelectorAll("[data-fetch]").forEach(b => b.onclick = async () => {
    const out = $("#libOut");
    out.textContent = "Working… this can take a few minutes.";
    b.disabled = true;
    try {
      const r = await api("/api/libs/fetch", { method: "POST", body: { source: b.dataset.fetch } });
      out.textContent = r.log.join("\n") + (r.ok ? `\nDone: ${r.status.footprints} footprints.` : "\nFailed.");
      loadStatus(true);
    } catch (e) { out.textContent = e.message; } finally { b.disabled = false; }
  });

  $("#promptForm").addEventListener("submit", async e => {
    e.preventDefault();
    const prompt = $("#prompt").value.trim();
    if (!prompt) return;
    if (!S.status.ai.ready && !confirm("The AI is not configured yet. Start anyway?")) return;
    const p = await api("/api/projects", { method: "POST", body: { prompt } });
    $("#prompt").value = "";
    openProject(p.id);
  });
  $("#importFile").addEventListener("change", async e => {
    const picked = [...e.target.files];
    e.target.value = "";
    if (!picked.length) return;
    let ai = S.status.ai.ready;
    if (!ai && !confirm("The AI is not configured, so the imported design will be built as it is. Continue?")) return;
    const read = f => new Promise((ok, bad) => {
      const r = new FileReader();
      r.onload = () => ok({ name: f.name, data: String(r.result).split(",", 2)[1] || "" });
      r.onerror = () => bad(r.error);
      r.readAsDataURL(f);
    });
    try {
      const files = await Promise.all(picked.map(read));
      const p = await api("/api/import", { method: "POST", body: { files, ai } });
      openProject(p.id);
    } catch (err) { alert("Import failed: " + err.message); }
  });
  $("#chatForm").addEventListener("submit", async e => {
    e.preventDefault();
    const text = $("#chatInput").value.trim();
    if (!text || !S.pid) return;
    $("#chatInput").value = "";
    await api(`/api/projects/${S.pid}/message`, { method: "POST", body: { text } });
  });
  $("#chatInput").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chatForm").requestSubmit(); }
  });
  const netState = () => {
    const a = S.status?.auth || {};
    $("#netState").textContent = (a.enabled ? "A password is set." : "No password: anyone who can reach this computer can use WholeBoard.")
      + (a.local ? "" : " Only the computer running WholeBoard can change it.");
    for (const id of ["#netSet", "#netClear", "#netPassword"]) $(id).disabled = !a.local;
  };
  $("#settingsTabs").addEventListener("click", e => { if (e.target.dataset.tab === "net") netState(); });
  const setPw = async password => {
    try {
      const r = await api("/api/auth/password", { method: "POST", body: { password } });
      S.status.auth.enabled = r.enabled; $("#netPassword").value = "";
      $("#netMsg").textContent = r.enabled ? "Password set. Other computers must log in." : "Password removed.";
      netState();
    } catch (err) { $("#netMsg").textContent = err.message; }
  };
  $("#netSet").onclick = () => { const v = $("#netPassword").value; if (v.length < 6) { $("#netMsg").textContent = "Use at least 6 characters."; return; } setPw(v); };
  $("#netClear").onclick = () => { if (confirm("Remove the password? Anyone on the network can then use WholeBoard.")) setPw(""); };
  $("#historyBtn").onclick = () => openHistory().catch(e => { $("#historyList").textContent = e.message; });
  $("#outlineBtn").onclick = () => $("#outlineFile").click();
  $("#outlineFile").onchange = async ev => {
    const file = ev.target.files[0]; ev.target.value = "";
    if (!file) return;
    const buf = new Uint8Array(await file.arrayBuffer());
    let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    try { await api(`/api/projects/${S.pid}/outline`, { method: "POST", body: { name: file.name, data: btoa(bin) } }); }
    catch (e) { alert(e.message); }
  };
  $("#clearListBtn").onclick = async () => {
    if (!confirm("Clear the boards list? The boards and their files stay on disk, and you can show them again.")) return;
    await api("/api/projects/clear", { method: "POST", body: {} });
    loadProjects();
  };
  $("#restoreListBtn").onclick = async () => { await api("/api/projects/clear", { method: "POST", body: { restore: true } }); loadProjects(); };
  $("#stopBtn").onclick = () => api(`/api/projects/${S.pid}/cancel`, { method: "POST" });
  $("#deleteBtn").onclick = async () => {
    if (!confirm("Delete this board and all its files? This cannot be undone.")) return;
    try { await api(`/api/projects/${S.pid}`, { method: "DELETE" }); showHome(); }
    catch (e) { alert(e.message); }
  };
  $("#folderBtn").onclick = async () => {
    const r = await api(`/api/projects/${S.pid}/open-folder`, { method: "POST" });
    if (!r.ok) alert("Folder: " + r.folder);
  };
  $("#editSpecBtn").onclick = async () => {
    const data = await api(`/api/projects/${S.pid}`);
    if (!data.state.spec) { alert("There is no design yet."); return; }
    openSpecDialog("design", data.state.spec);
  };
  $("#specSave").onclick = async () => {
    let spec;
    try { spec = JSON.parse($("#specText").value); } catch (e) { $("#specErr").textContent = "Not valid JSON: " + e.message; return; }
    try {
      const what = S.specMode === "enclosure" ? "enclosure" : "spec";
      $("#specDialog").close();
      const r = await api(`/api/projects/${S.pid}/${what}`, { method: "PUT", body: spec });
      if (what === "enclosure" && r.errors?.length) { $("#specErr").textContent = r.errors.join("\n"); $("#specDialog").showModal(); }
    } catch (e) { $("#specErr").textContent = e.message; }
  };

  loadStatus().then(() => {
    const pid = location.hash.slice(1);
    loadProjects();
    if (pid) openProject(pid).catch(() => showHome());
  });
}

init();

// ---- colour themes
(function themes() {
  const sel = document.getElementById("themeSelect");
  let cur = "auto";
  try { cur = localStorage.getItem("wb-theme") || "auto"; } catch (e) { /* storage blocked */ }
  if (![...sel.options].some(o => o.value === cur)) cur = "auto";
  const apply = t => {
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem("wb-theme", t); } catch (e) { /* storage blocked */ }
    window.dispatchEvent(new Event("wb-theme"));
  };
  sel.value = cur;
  apply(cur);
  sel.addEventListener("change", () => apply(sel.value));
})();

// ---- zoom the whole interface
(function zoom() {
  const steps = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
  const label = $("#zoomReset");
  const apply = z => {
    document.documentElement.style.zoom = z === 1 ? "" : z;
    label.textContent = Math.round(z * 100) + "%";
    $("#zoomOut").disabled = z <= steps[0]; $("#zoomIn").disabled = z >= steps[steps.length - 1];
    try { localStorage.setItem("wb-zoom", z); } catch (e) { /* storage blocked */ }
    window.dispatchEvent(new Event("wb-zoom"));   // canvases re-size their backing stores
    window.dispatchEvent(new Event("resize"));    // and the tour re-places its spotlight
  };
  const step = dir => {
    const z = wbZoom();
    apply(dir > 0 ? (steps.find(s => s > z + 1e-3) ?? z) : ([...steps].reverse().find(s => s < z - 1e-3) ?? z));
  };
  $("#zoomIn").onclick = () => step(1);
  $("#zoomOut").onclick = () => step(-1);
  label.onclick = () => apply(1);
  // Ctrl+Alt with + / - / 0, so the browser's own zoom keys still work
  window.addEventListener("keydown", e => {
    if (!e.ctrlKey || !e.altKey) return;
    if (e.key === "=" || e.key === "+") step(1);
    else if (e.key === "-") step(-1);
    else if (e.key === "0") apply(1);
    else return;
    e.preventDefault();
  });
  apply(wbZoom());
})();

// ---- drag handles that resize the prompt and message boxes
(function resizers() {
  for (const grip of document.querySelectorAll(".resize-grip")) {
    const ta = document.getElementById(grip.dataset.for), key = ta.dataset.resize, up = grip.hasAttribute("data-up");
    try { const h = parseFloat(localStorage.getItem(key)); if (h > 0) ta.style.height = h + "px"; } catch (e) { /* storage blocked */ }
    let start = null;
    grip.addEventListener("pointerdown", e => {
      start = [e.clientY, ta.getBoundingClientRect().height / wbZoom()];
      grip.setPointerCapture(e.pointerId); grip.classList.add("drag"); e.preventDefault();
    });
    grip.addEventListener("pointermove", e => {
      if (!start) return;
      const dy = (e.clientY - start[0]) / wbZoom();
      const max = window.innerHeight / wbZoom() * 0.7;
      ta.style.height = Math.max(36, Math.min(max, start[1] + (up ? -dy : dy))) + "px";
    });
    const end = () => {
      if (!start) return;
      start = null; grip.classList.remove("drag");
      try { localStorage.setItem(key, parseFloat(ta.style.height)); } catch (e) { /* storage blocked */ }
    };
    grip.addEventListener("pointerup", end);
    grip.addEventListener("pointercancel", end);
    grip.addEventListener("dblclick", () => { ta.style.height = ""; try { localStorage.removeItem(key); } catch (e) { /* storage blocked */ } });
  }
})();
