"use strict";
// "Take a tour": walks through a normal WholeBoard workflow, highlighting each part of the page in turn.
// Steps that need a board open one of yours (a finished one if there is one); without any, they are skipped.

const TOUR = [
  { sel: "#promptForm", title: "1. Describe your board", home: true,
    text: "Say what the board should do in plain words: the parts you want, how it is powered, the connectors and roughly how big. The designer asks if anything is unclear." },
  { sel: "#examples", title: "Or start from an example", home: true,
    text: "These fill in a typical request: a USB-C LED board, an ESP32-C3 sensor, a 555 blinker or an RP2040 breakout." },
  { sel: "label[for=importFile]", title: "Bring an existing KiCad project", home: true,
    text: "Import a .kicad_sch and .kicad_pcb (or a zip) to check, simulate, re-route and quote a design you already have." },
  { sel: "#statusChips", title: "Your tools", home: true,
    text: "WholeBoard drives KiCad, the TraceMaker autorouter, ngspice and FreeCAD. A green dot means it found the tool; the last chip is the AI model doing the design." },
  { sel: "#settingsBtn", title: "Settings", home: true,
    text: "Choose the AI (Claude, or any OpenAI-compatible model, local ones included), an optional expert model to plan and review, your JLCPCB options, tools and network access." },
  { sel: "#projectList", title: "Your boards", home: true,
    text: "Every board you design is kept here with its status and price. Click one to open it." },
  { sel: ".chat-pane", title: "2. Talk to the designer", project: true,
    text: "The designer explains its plan, asks questions with quick answers, and reports what each tool found. Ask for changes here at any time, for example “use a USB-C connector” or “make it 5 mm narrower”." },
  { sel: "#stepper", title: "3. Follow the build, step by step", project: true,
    text: "Every board goes through the same checked steps. A dot shows each one's state; click any step to see what it produced." },
  { sel: '.vis[data-kind="svg"]', step: "schematic", title: "Schematic and electrical rules check", project: true,
    text: "A real KiCad schematic, checked by KiCad's ERC. Zoom and pan it here, or open it in KiCad." },
  { sel: '.vis[data-kind="sim"]', step: "sim", title: "4. Simulation", project: true,
    text: "ngspice runs the circuit before anything is placed: rail voltages, currents, every part against its ratings, heat, battery life and timing. Problems go back to the designer to fix." },
  { sel: '.vis[data-kind="simlab"]', step: "sim", title: "Sim Lab: the simulation on the 3D board", project: true,
    text: "Once the board is routed, see the current flow, LED brightness and a heat map on the 3D board. Click a risk to fly to it, and drag the sliders to re-run the simulation with other values." },
  { sel: ".simlab-actions", step: "sim", title: "A guided tour or a video", project: true,
    text: "Play tour flies through the results; Record video saves the same tour as an MP4 you can share." },
  { sel: '.vis[data-kind="power"]', step: "sim", title: "Power budget", project: true,
    text: "What each supply and regulator delivers against what it can, the loads on each rail and the hottest parts." },
  { sel: '.vis[data-kind="board"], .vis[data-kind="viewer"]', step: "route", title: "5. Placement and routing", project: true,
    text: "TraceMaker places the parts and routes every connection. While it runs you can watch it live; afterwards the routed board shows here with pan, zoom and layer toggles." },
  { sel: '.vis[data-kind="drc"], .vis[data-kind="checks"]', step: "drc", title: "Design rules and board checks", project: true,
    text: "KiCad's DRC, then the tracks are checked for heating and voltage drop at the simulated currents, and decoupling capacitors for distance from their chips." },
  { sel: '.vis[data-kind="images"]', step: "outputs", title: "6. 3D renders", project: true,
    text: "KiCad renders of the finished board with real part models. The designer looks at these too, like a reviewer would." },
  { sel: '.vis[data-kind="files"]', step: "outputs", title: "Files for manufacturing", project: true,
    text: "Gerbers, drill, BOM and placement files ready for JLCPCB, the KiCad project, and an interactive assembly page." },
  { sel: '.vis[data-kind="bringup"], .vis[data-kind="firmware"], .vis[data-kind="report"]', step: "outputs", title: "Bring-up, firmware and the report", project: true,
    text: "A checklist for powering the board up the first time, starter firmware you can compile and flash from the browser, and a PDF report of the whole project." },
  { sel: '.vis[data-kind="quote"]', step: "quote", title: "7. The quote", project: true,
    text: "The full JLCPCB price: boards, assembly, parts and shipping. Try other options, swap in cheaper parts, compare OSH Park and PCBWay, and check live stock before you order." },
  { sel: '.vis[data-kind="enclosure"], .vis[data-kind="case"]', step: "enclosure", title: "8. An enclosure", project: true,
    text: "The designer can plan a 3D-printable box that FreeCAD builds and checks for collisions, or fit the board to a box you already have." },
  { sel: ".ws-actions", title: "Your board's tools", project: true,
    text: "History compares and restores earlier builds; Board outline gives the board a shape from a DXF; Edit design JSON changes the design by hand; Open folder shows every file." },
  { sel: "#logBox", title: "Tool output", project: true,
    text: "The raw output of each tool for the selected step, if you want the detail." },
  { sel: "#tourBtn", title: "That's the workflow", home: null,
    text: "Describe, check, simulate, route, review, quote and box. Take this tour again from here whenever you like." },
];

let tourState = null;

async function startTour() {
  if (tourState) return;
  const root = el("div", { class: "tour-root" });
  const spot = el("div", { class: "tour-spot" });
  const pop = el("div", { class: "tour-pop", role: "dialog", "aria-live": "polite" });
  root.append(spot, pop);
  document.body.append(root);
  tourState = { i: 0, root, spot, pop, startPid: S.pid, demoPid: null };
  const key = e => {
    if (e.key === "Escape") endTour();
    else if (e.key === "ArrowRight") tourGo(1);
    else if (e.key === "ArrowLeft") tourGo(-1);
  };
  const place = () => tourPlace();
  tourState.cleanup = () => { document.removeEventListener("keydown", key); window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  document.addEventListener("keydown", key);
  window.addEventListener("resize", place);
  window.addEventListener("scroll", place, true);
  // a board to show the later steps on: the open one, else your newest finished board
  if (!S.projects?.length) await loadProjects();
  tourState.demoPid = S.pid || (S.projects.find(p => p.status === "done") || S.projects[0])?.id || null;
  tourShow(0);
}

function endTour() {
  if (!tourState) return;
  const back = tourState.startPid;
  tourState.cleanup();
  tourState.root.remove();
  tourState = null;
  // put the page back the way it was
  if (back && S.pid !== back) openProject(back);
  else if (!back && S.pid) showHome();
}

function tourGo(d) {
  if (!tourState) return;
  tourShow(tourState.i + d, d);
}

async function tourWaitFor(sel, ms = 5000) {
  const t0 = performance.now();
  while (performance.now() - t0 < ms) {
    const n = [...document.querySelectorAll(sel)].find(x => x.offsetParent !== null || x.getClientRects().length);
    if (n) return n;
    await new Promise(r => setTimeout(r, 120));
  }
  return null;
}

async function tourShow(i, dir = 1) {
  const st = tourState;
  if (!st) return;
  if (i < 0) i = 0;
  if (i >= TOUR.length) { endTour(); return; }
  const step = TOUR[i];
  const pid = st.demoPid;
  // steps that need a board are skipped when there is none
  if (step.project && !pid) { tourShow(i + dir, dir); return; }
  st.i = i;
  st.pop.classList.add("busy");
  if (step.home === true && S.pid) showHome();
  if (step.project && S.pid !== pid) await openProject(pid);
  if (step.project && step.step) {
    S.follow = false;
    if (S.selStep !== step.step) selectStep(step.step, true);
  }
  if (!tourState || tourState !== st || st.i !== i) return;      // the user moved on while this loaded
  let target = await tourWaitFor(step.sel, step.step ? 6000 : 2500), missing = false;
  if (!tourState || st.i !== i) return;
  if (!target && step.step) { target = document.getElementById("step-" + step.step); missing = true; }   // point at the step instead
  st.target = target;
  st.pop.classList.remove("busy");
  if (target) target.scrollIntoView({ block: "nearest", inline: "nearest" });
  const shown = TOUR.filter(s => !s.project || pid).length, n = TOUR.slice(0, i + 1).filter(s => !s.project || pid).length;
  const demo = step.project && st.demoPid !== st.startPid && i === TOUR.findIndex(s => s.project)
    ? el("p", { class: "muted" }, "Showing one of your boards as the example.") : "";
  st.pop.innerHTML = "";
  st.pop.append(
    el("div", { class: "tour-count" }, `${n} of ${shown}`),
    el("h4", {}, step.title), el("p", {}, step.text), demo,
    missing ? el("p", { class: "muted" }, "This board has not got this yet; it shows under this step when it does.") : "",
    el("div", { class: "tour-btns" },
      el("button", { class: "btn ghost", type: "button", onclick: endTour }, "Skip tour"),
      el("span", { class: "spacer" }),
      i > 0 ? el("button", { class: "btn", type: "button", onclick: () => tourGo(-1) }, "Back") : "",
      el("button", { class: "btn primary", type: "button", onclick: () => tourGo(1) }, i === TOUR.length - 1 ? "Done" : "Next")));
  if (!pid && i === TOUR.length - 1) st.pop.querySelector("p").append(" Design your first board to see the steps after the request.");
  tourPlace();
  st.pop.querySelector(".btn.primary")?.focus({ preventScroll: true });
}

function tourPlace() {
  const st = tourState;
  if (!st) return;
  // positions are in layout pixels: the page may be zoomed
  const t = st.target, pad = 6, z = wbZoom(), vw = window.innerWidth / z, vh = window.innerHeight / z;
  const pw = Math.min(360, vw - 24);
  st.pop.style.width = pw + "px";
  if (!t) {
    st.spot.style.cssText = `left:${vw / 2}px;top:${vh / 2}px;width:0;height:0`;
    st.pop.style.left = (vw - pw) / 2 + "px"; st.pop.style.top = vh / 3 + "px";
    return;
  }
  const b = t.getBoundingClientRect(), r = { left: b.left / z, top: b.top / z, right: b.right / z, bottom: b.bottom / z };
  const x = Math.max(4, r.left - pad), y = Math.max(4, r.top - pad);
  const w = Math.min(vw - 8, r.right + pad) - x, h = Math.min(vh - 8, r.bottom + pad) - y;
  st.spot.style.cssText = `left:${x}px;top:${y}px;width:${w}px;height:${h}px`;
  const ph = st.pop.offsetHeight || 200;
  let left, top;
  if (vh - (y + h) > ph + 16) { top = y + h + 12; left = x; }                 // below
  else if (y > ph + 16) { top = y - ph - 12; left = x; }                    // above
  else if (vw - (x + w) > pw + 16) { left = x + w + 12; top = y; }          // right
  else if (x > pw + 16) { left = x - pw - 12; top = y; }                    // left
  else { left = x + w / 2 - pw / 2; top = y + Math.min(h, vh) / 2 - ph / 2; } // over the middle of a big target
  st.pop.style.left = Math.max(12, Math.min(vw - pw - 12, left)) + "px";
  st.pop.style.top = Math.max(12, Math.min(vh - ph - 12, top)) + "px";
}
