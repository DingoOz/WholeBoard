"""The design agent: talks to the user, picks parts, writes the spec, iterates on results."""

from __future__ import annotations

import json
import re
import threading
import traceback
import uuid
from pathlib import Path

from .. import config, design, pipeline, sim
from ..jlc import parts as jparts
from ..kicad import libs, schematic
from ..project import Project
from .providers import AIError, make_session

SYSTEM = """You are WholeBoard's PCB design engineer. You turn a user's description into a \
manufacturable printed circuit board that JLCPCB can fabricate and assemble. You work through \
tools: KiCad libraries for footprints and symbols, JLCPCB's parts library, and a build tool that \
generates the schematic and board, places and routes it with the TraceMaker autorouter, runs \
KiCad's design rule check, exports fab files and prices the job.

How to work
1. Understand the requirements. If anything that changes the design is unclear (supply \
voltage, connectors, size limits, quantities, interfaces, must-use parts), ask with ask_user \
before designing. Ask only what matters, one question per call, offering options when you \
can. Do not ask about things you can decide sensibly yourself; state those assumptions instead. \
Unless the user already said, ask whether they want a 3D-printable enclosure, and if so whether \
the board should screw into it (then add mounting holes).
2. Choose the architecture and parts. For each part find a JLCPCB part with search_parts \
(prefer "basic" or "preferred" parts with good stock; "extended" parts add a feeder fee) and a \
matching KiCad footprint with search_footprints. Check pin numbering with footprint_info and, \
for ICs, symbol_info: nets connect FOOTPRINT PAD NAMES, so map every IC pin name to its pad \
number from the symbol (KiCad symbol pin numbers equal footprint pad names).
3. Write the design, including a "simulation" section: the supplies (voltage, kind, current \
limit, battery capacity) and "expect" entries for what the circuit must do (rail voltages, \
oscillator frequency, LED currents, filter gain). Call simulate to check the circuit in ngspice \
in seconds; fix every problem it reports (overloaded resistors, LED overcurrent, regulator \
dropout or overheating, parts outside their supply range, failed expectations). Then call \
submit_design. It validates the spec, draws the schematic and runs KiCad's ERC, simulates, \
builds the board, checks trace heating, voltage drop and decoupling-capacitor distances, and \
reports back. Fix any errors it reports and submit again (always the complete spec). Typical \
fixes: wrong pad names, a bigger board, moving connectors, 4 layers for dense designs, cheaper \
or in-stock parts, a placement next to the IC for a far decoupling capacitor, wider power tracks.
3b. When the build succeeds you get renders of the board (if your model can see images). Look \
at them as an experienced reviewer would: parts off the board or overlapping, connectors facing \
inwards or blocked, LEDs or buttons hidden, silkscreen problems, odd placement. Call \
report_review with what you found; if anything is a real problem, fix it and submit again.
The quote's cheaper_parts lists drop-in replacements (same value and package, or the same \
part number) that are basic/preferred or in stock. Mention the saving to the user; they can apply \
it from the quote card, or you can use those LCSC numbers the next time you submit the design.
4. When the build is clean and quoted, design the 3D-printable enclosure, unless the user does \
not want one. Call enclosure_context to see the board's size, mounting holes and every part \
with its position, estimated height, kind and nearest edge, plus a suggested starting design. \
Decide what the box needs: openings in the walls for connectors and anything the user plugs in \
or reaches, lid holes for LEDs, buttons and switches, how the board is held and how the lid \
closes, vents for parts that get warm, and a label if the user wants one. Give datasheet \
heights in part_heights for tall parts whose estimate looks wrong. Ask the user about real \
choices (screwed or press-fit lid, wall mounting, label text) if they have not said. Then call \
build_enclosure; FreeCAD builds it and reports collisions between parts and the box. Fix every \
collision (bigger openings, more headroom, a different wall) and build again.
5. Call finish with a short summary for the user: what the board does, key parts, board size \
and layers, the quote total, the enclosure size and features, and anything they should check.

Good engineering rules
- Add the support parts real circuits need: decoupling capacitors (100 nF per IC supply pin \
plus bulk capacitance), pull-ups, ESD protection on external connectors where sensible, USB-C \
CC pull-down resistors (5.1 kΩ) for sinks, reset/boot circuitry for MCUs, crystal load caps.
- Single-sided SMT assembly on the top side. Prefer 0603 or 0402 passives (0603 is easiest).
- Mark supply nets (GND, VBUS, +5V, +3V3 ...) with class "power" for wider tracks. Name the \
ground net exactly GND so the ground pour connects to it.
- Fix connectors, switches, LEDs the user will look at, and mounting holes with a placement \
(x_mm, y_mm from the board's top-left corner, rotation in degrees, locked true). Put \
connectors at the board edge with the courtyard inside the outline. Leave everything else \
without placement: TraceMaker places it.
- Size the board for the parts: about 2.5 times the total courtyard area for a 2-layer board, \
unless the user gave a size. Use 4 layers for MCUs with many nets in a small area or for \
high-speed signals, with planes {"In1.Cu": "GND", "In2.Cu": "<main supply>"}.
- Every pad must be in a net or listed in the component's no_connect list.
- Rate parts for what the simulation shows: resistor power (0603 = 100 mW), LED current, \
regulator heat ((Vin - Vout) × current), capacitor voltage (with margin), switch current.
- JLCPCB rotation offsets for common packages are applied to the CPL automatically. If a part \
needs a different correction, give the component "cpl": {"rotation": 90, "dx_mm": 0, "dy_mm": 0}.

Design spec (the `spec` argument of submit_design)
{
  "name": "short_snake_case_name",
  "summary": "one paragraph describing the board",
  "board": {"width_mm": 50, "height_mm": 30, "layers": 2, "thickness_mm": 1.6,
            "corner_radius_mm": 1, "mounting_holes": {"diameter_mm": 3.2, "inset_mm": 3.5} or null,
            "ground_pour": "GND", "planes": {}},
  "rules": {"track_mm": 0.25, "power_track_mm": 0.5, "clearance_mm": 0.2,
            "via_diameter_mm": 0.6, "via_drill_mm": 0.3},
  "components": [
    {"ref": "U1", "value": "AMS1117-3.3", "footprint": "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
     "symbol": "Regulator_Linear:AMS1117-3.3", "lcsc": "C6186", "mpn": "AMS1117-3.3",
     "description": "3.3 V LDO", "no_connect": [],
     "placement": {"x_mm": 10, "y_mm": 5, "rotation": 0, "locked": true}}
  ],
  "nets": [
    {"name": "GND", "class": "power", "pins": ["U1.1", "C1.2"]},
    {"name": "+3V3", "class": "power", "pins": ["U1.2", "C1.1"]}
  ]
}
- "footprint" and "symbol" are KiCad library ids ("Library:Name") that the search tools return. \
"symbol" is optional; without it the schematic shows a generated box.
- Pins are "REF.PAD". Mounting holes from board.mounting_holes need no component entries.
- "rules" is optional; the defaults suit JLCPCB's standard process.
- "simulation" and per-component "sim" are described below.

{SIM_DOC}

Common JLCPCB basic parts (checked October 2026; confirm stock with search_parts):
0603 resistors 1% (Resistor_SMD:R_0603_1608Metric): 0R C21189, 100R C22775, 1k C21190,
4.7k C23162, 5.1k C23186, 10k C25804, 100k C25803. 0402 10k C25744.
0603 capacitors (Capacitor_SMD:C_0603_1608Metric): 22pF C1653, 10nF C57112, 100nF C14663,
1uF C15849, 4.7uF C19666. 0402 100nF C1525. 0805 (C_0805_2012Metric): 10uF C15850, 22uF C45783.
LEDs (LED_SMD:LED_0603_1608Metric, pad 1 = cathode): red KT-0603R C2286, white KT-0603W C2290;
green 0805 KT-0805G C2297 (LED_0805_2012Metric).
Regulators: AMS1117-3.3 C6186 (SOT-223-3_TabPin2), XC6206P332MR 3.3 V 200 mA C5446 (SOT-23).
Others: AO3400A N-MOSFET C20917 (SOT-23), tactile switch TS-1187A C318884 (5.1x5.1 mm),
12 MHz crystal 3225 C9002, USB-C 16P receptacle TYPE-C-31-M-12 C165948 (extended).
Use find_passive for other resistor, capacitor and LED values.

Keep messages to the user short and plain. Explain decisions briefly as you go.""".replace(
    "{SIM_DOC}", sim.SIM_DOC)


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required}}


TOOLS = [
    _tool("ask_user", "Ask the user one question and wait for the answer. Offer short options "
          "when the answer is a choice; the user can always type their own answer.",
          {"question": {"type": "string"},
           "options": {"type": "array", "items": {"type": "string"},
                       "description": "Suggested answers (optional)."}},
          ["question"]),
    _tool("search_parts", "Search JLCPCB's parts library (in-stock parts only). It matches "
          "manufacturer part numbers best: 'AMS1117-3.3', 'BME280', 'ESP32-C3-MINI-1', 'TYPE-C-31-M-12', "
          "or an LCSC number such as 'C6186'. Short keyword phrases also work: 'USB Type C', "
          "'header 4P 2.54mm'. Use find_passive for resistors, capacitors and LEDs. Returns LCSC "
          "numbers, package, stock, basic/preferred/extended and unit prices.",
          {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    _tool("find_passive", "Find in-stock JLCPCB resistors, capacitors or LEDs by value and package, "
          "basic parts first. value: '10k', '4k7', '100nF', '22uF', or an LED colour such as 'green'.",
          {"kind": {"type": "string", "enum": ["resistor", "capacitor", "led"]},
           "value": {"type": "string"}, "package": {"type": "string", "description": "0402, 0603, 0805, 1206"}},
          ["kind", "value"]),
    _tool("search_footprints", "Search KiCad's footprint libraries. Words must all appear in the "
          "'Library:Name' id, e.g. 'R_0603', 'SOT-23-5', 'USB_C Receptacle 16P', 'LQFP-48 7x7', "
          "'PinHeader_1x04 P2.54 Vertical'.",
          {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    _tool("footprint_info", "Pads, size and mounting type of a KiCad footprint.",
          {"footprint": {"type": "string"}}, ["footprint"]),
    _tool("search_symbols", "Search KiCad's schematic symbol libraries, e.g. 'STM32F103C8', "
          "'AMS1117', 'Device:R', 'USB_C_Receptacle'.",
          {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    _tool("symbol_info", "Pin numbers, names and types of a KiCad symbol, and its default "
          "footprint. Use it to map IC pin names to footprint pad numbers.",
          {"symbol": {"type": "string"}}, ["symbol"]),
    _tool("check_design", "Validate a design spec without building it: footprints, pad names, "
          "nets, board area and JLCPCB part stock. Quick; use it before submit_design on big designs.",
          {"spec": {"type": "object"}}, ["spec"]),
    _tool("simulate", "Simulate a design spec with ngspice (seconds): rail voltages, supply currents and "
          "battery life, oscillator frequencies, every part's current, power and temperature against its "
          "rating, regulator dropout, IC supply ranges and your simulation.expect checks. Returns the "
          "problems to fix and the assumptions the models made.",
          {"spec": {"type": "object", "description": "The complete design spec, with a simulation section."}},
          ["spec"]),
    _tool("submit_design", "Validate and build the complete design: schematic and ERC, ngspice "
          "simulation, board, TraceMaker placement and routing, KiCad DRC with ground pour, trace-current "
          "and decoupling checks, 3D renders, Gerbers/BOM/CPL and the JLCPCB quote. Takes a few minutes. "
          "Returns what passed, or the problems to fix.",
          {"spec": {"type": "object", "description": "The complete design spec."}}, ["spec"]),
    _tool("report_review", "Report your visual review of the board renders: each issue with severity "
          "'problem' (must fix: you will fix and resubmit) or 'note' (worth the user knowing).",
          {"issues": {"type": "array", "items": {"type": "object", "properties": {
              "severity": {"type": "string", "enum": ["problem", "note"]}, "text": {"type": "string"}},
              "required": ["severity", "text"]}},
           "summary": {"type": "string", "description": "One or two sentences."}},
          ["issues", "summary"]),
    _tool("enclosure_context", "Facts for designing the enclosure of the finished board: board size, "
          "mounting holes, every part with position (mm from the board's top-left), courtyard, estimated "
          "height, kind (port, led, user, header, part) and nearest edge, a suggested starting design, "
          "the current enclosure design if any, and the enclosure spec format.", {}, []),
    _tool("build_enclosure", "Build the enclosure in FreeCAD from an enclosure spec (format in "
          "enclosure_context). Returns sizes, openings, lid holes, collisions between parts and the box, "
          "and notes. Takes a few seconds.",
          {"enclosure": {"type": "object", "description": "The complete enclosure spec."}}, ["enclosure"]),
    _tool("finish", "Finish: the board is built, checked and quoted. Give the user a short summary.",
          {"summary": {"type": "string"}}, ["summary"]),
]


# OpenAI-compatible models that accept images (others skip the visual review unless ai.vision is "on").
VISION_MODELS = re.compile(r"gpt-4o|gpt-4\.1|gpt-4-turbo|gpt-5|\bo[134]\b|o[34]-|grok-4|grok-[23].*vision|vision|"
                           r"llava|-vl\b|vl-|qwen.*vl|gemma-?3|pixtral|minicpm-v|llama-?3\.2-?\d*b?-?vision|"
                           r"mistral-small-3|moondream|glm-4\.?\dv", re.I)


class Agent:
    def __init__(self, project: Project, view_origin: str | None = None):
        self.p = project
        self.view_origin = view_origin

    # ------------------------------------------------------------ lifecycle
    def start(self, user_text: str) -> None:
        p = self.p
        with p.lock:
            if p.worker and p.worker.is_alive():
                p.inbox.append(user_text)
                p.chat("user", user_text)
                return
            p.cancel.clear()
            p.worker = threading.Thread(target=self._run, args=(user_text,), daemon=True,
                                        name=f"agent-{p.id}")
        p.chat("user", user_text)
        p.worker.start()

    def _new_session(self):
        ai = config.load()["ai"]
        sess = make_session(ai, SYSTEM, TOOLS)
        self.p.state["ai_label"] = sess.label
        return sess

    def _run(self, user_text: str) -> None:
        p = self.p
        try:
            if p.session is None:
                p.session = self._new_session()
                if p.state.get("spec"):
                    user_text = ("The current design spec (revision "
                                 f"{p.state.get('revision')}) is:\n```json\n"
                                 f"{json.dumps(p.state['spec'])}\n```\n\n{user_text}")
            p.set_status("running", f"Working with {p.session.label}")
            if p.state["steps"]["brief"]["status"] == "pending":
                p.step("brief", "running", "Reading the request")
            p.session.add_user(user_text)
            self._loop()
        except pipeline.Cancelled:
            p.set_status("stopped", "Stopped by the user")
        except AIError as e:
            p.chat("system", f"AI error: {e}")
            p.set_status("error", str(e))
        except Exception as e:  # noqa: BLE001
            p.chat("system", f"Unexpected error: {e}")
            p.log("agent", traceback.format_exc())
            p.set_status("error", str(e))

    def _loop(self) -> None:
        p = self.p
        max_turns = int(config.load()["ai"].get("max_turns", 60))
        for _ in range(max_turns):
            if p.cancel.is_set():
                raise pipeline.Cancelled()
            turn = p.session.step()
            self._count(turn.usage)
            if turn.note:
                p.log("agent", turn.note)
            if turn.text:
                p.chat("assistant", turn.text)
            if turn.stop == "refusal":
                p.chat("system", "The AI declined this request.")
                p.set_status("idle", "Waiting for your message")
                return
            if not turn.calls:
                if turn.stop == "max_tokens":
                    p.session.add_user("Your last reply was cut off. Continue.")
                    continue
                with p.lock:
                    pending = p.inbox[:]
                    p.inbox.clear()
                if pending:
                    p.session.add_user("\n\n".join(pending))
                    continue
                done = p.state.get("finished")
                p.set_status("done" if done else "idle",
                             "Board ready" if done else "Waiting for your message")
                return
            results = []
            for call in turn.calls:
                if p.cancel.is_set():
                    raise pipeline.Cancelled()
                if call.error:
                    results.append((call.id, f"Invalid call: {call.error}. Send the call again.", True))
                    continue
                try:
                    out = self._execute(call.name, call.input)
                    results.append((call.id, out, False))
                except pipeline.Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001
                    results.append((call.id, f"{type(e).__name__}: {e}", True))
            with p.lock:
                pending = p.inbox[:]
                p.inbox.clear()
            extra = ("Message from the user while you were working: " + "\n\n".join(pending)) if pending else None
            p.session.add_tool_results(results, extra)
            p.set_status("running", f"Working with {p.session.label}")
        p.chat("system", "Reached the turn limit for one run. Send a message to continue.")
        p.set_status("idle", "Turn limit reached")

    # ------------------------------------------------------------ tools
    def _execute(self, name: str, args: dict) -> str:
        p = self.p
        if name == "ask_user":
            return self._ask(str(args["question"]), [str(o) for o in args.get("options") or []])
        if name == "search_parts":
            if p.state["steps"]["parts"]["status"] == "pending":
                self._brief_done()
                p.step("parts", "running", "Choosing parts")
            q = str(args["query"])
            res = jparts.search(q, int(args.get("limit") or 8))
            p.emit({"type": "tool", "name": name, "text": f"Searched JLCPCB parts for “{q}”: {len(res)} found"})
            return json.dumps([{k: r[k] for k in ("lcsc", "mpn", "manufacturer", "package", "description",
                                                    "stock", "library_type")}
                               | {"unit_price": jparts.unit_price(r, 10)} for r in res])
        if name == "find_passive":
            if p.state["steps"]["parts"]["status"] == "pending":
                self._brief_done()
                p.step("parts", "running", "Choosing parts")
            res = jparts.find_passive(str(args["kind"]), str(args["value"]), str(args.get("package") or "0603"))
            p.emit({"type": "tool", "name": name,
                    "text": f"Looked for {args['value']} {args['kind']} ({args.get('package') or '0603'}): {len(res)} in stock"})
            return json.dumps([{k: r[k] for k in ("lcsc", "mpn", "package", "description", "stock", "library_type")}
                               | {"unit_price": jparts.unit_price(r, 10)} for r in res]) or "[]"
        if name == "search_footprints":
            q = str(args["query"])
            res = libs.search_footprints(q, int(args.get("limit") or 30))
            p.emit({"type": "tool", "name": name, "text": f"Searched footprints for “{q}”: {len(res)} found"})
            return json.dumps(res) if res else "No footprints matched; try fewer or different words."
        if name == "footprint_info":
            fp = libs.load_footprint(str(args["footprint"]))
            p.emit({"type": "tool", "name": name, "text": f"Read footprint {args['footprint']}"})
            if not fp:
                hint = libs.search_footprints(str(args["footprint"]).split(":")[-1].replace("_", " ")[:30], 8)
                return f"Footprint not found. Close matches: {json.dumps(hint)}"
            return json.dumps(fp.summary())
        if name == "search_symbols":
            q = str(args["query"])
            res = libs.search_symbols(q, int(args.get("limit") or 30))
            p.emit({"type": "tool", "name": name, "text": f"Searched symbols for “{q}”: {len(res)} found"})
            return json.dumps(res) if res else "No symbols matched."
        if name == "symbol_info":
            info = schematic.symbol_info(str(args["symbol"]))
            p.emit({"type": "tool", "name": name, "text": f"Read symbol {args['symbol']}"})
            return json.dumps(info) if info else "Symbol not found; use search_symbols."
        if name == "check_design":
            spec = args["spec"]
            rep = design.validate(spec)
            notes = pipeline.check_parts(spec) if rep.ok else []
            p.emit({"type": "tool", "name": name,
                    "text": f"Checked the design: {len(rep.errors)} errors, {len(rep.warnings)} warnings"})
            return json.dumps({**rep.as_dict(), "part_notes": notes})
        if name == "simulate":
            spec = args["spec"]
            rep = design.validate(spec)
            p.emit({"type": "tool", "name": name, "text": "Simulating the circuit with ngspice"})
            if not rep.ok:
                return json.dumps({"ok": False, "design_errors": rep.errors,
                                   "note": "Fix these first; the simulation needs a valid design."})
            from .. import ngspice
            from ..jlc import parts as jp
            cfg = config.load()
            out = p.root / "sim-check"

            def lookup(code):
                try:
                    return jp.lookup(code)
                except Exception:  # noqa: BLE001
                    return None
            res = sim.simulate(spec, out, cfg, ngspice.find(cfg), part_info=lookup, cancel=p.cancel)
            return json.dumps(pipeline.sim_summary(res), default=str)
        if name == "report_review":
            issues = [i for i in args.get("issues") or [] if isinstance(i, dict)]
            probs = [i for i in issues if i.get("severity") == "problem"]
            p.visual("review", "review", "AI visual review", summary=str(args.get("summary") or ""),
                     issues=issues)
            p.step("review", "warn" if probs else "done",
                   f"{len(probs)} problems, {len(issues) - len(probs)} notes" if issues else "Looks right")
            p.emit({"type": "tool", "name": name, "text": "Reviewed the board renders"})
            return ("Recorded. Fix the problems and submit again." if probs else "Recorded.")
        if name == "submit_design":
            self._brief_done()
            if p.state["steps"]["parts"]["status"] in ("pending", "running"):
                p.step("parts", "done", f"{len(args['spec'].get('components') or [])} parts chosen")
            p.emit({"type": "tool", "name": name, "text": "Building the design"})
            p.set_status("running", "Building the board")
            res = pipeline.build(p, args["spec"], self.view_origin, by="AI")
            if not res.get("ok"):
                return json.dumps(res, default=str)
            images = self._review_images() if self._vision() else []
            if images:
                p.step("review", "running", "The AI is looking at the renders")
                res["next_step"] = ("Review the attached renders (iso, top, bottom) and call report_review. "
                                    + ("Then design the enclosure (enclosure_context, build_enclosure) unless "
                                       "the user does not want one." if not p.state.get("enclosure_spec") else ""))
                return {"text": json.dumps(res, default=str), "images": images}
            p.step("review", "done", "Skipped: the AI model cannot see images")
            if not p.state.get("enclosure_spec"):
                res["next_step"] = ("The board is done. Design the enclosure now (enclosure_context, then "
                                    "build_enclosure) unless the user does not want one.")
            return json.dumps(res, default=str)
        if name == "enclosure_context":
            pcb = Path((p.state.get("artifacts") or {}).get("pcb") or "")
            if not p.state.get("spec") or not pcb.exists():
                return json.dumps({"error": "There is no finished board yet. Get a clean build first."})
            from .. import enclosure
            from ..kicad import geometry
            ctx = enclosure.context(geometry.read(pcb), design.normalise(p.state["spec"]),
                                    (p.state.get("enclosure_spec") or {}).get("part_heights"))
            p.emit({"type": "tool", "name": name, "text": "Read the finished board for the enclosure"})
            freecad = enclosure.find_freecad(config.load())
            return json.dumps({"board_context": enclosure.public_context(ctx),
                               "suggested_enclosure": enclosure.suggest(ctx),
                               "current_enclosure": p.state.get("enclosure_spec"),
                               "spec_format": enclosure.SPEC_DOC,
                               "freecad": "found" if freecad else "NOT FOUND: tell the user to install FreeCAD"})
        if name == "build_enclosure":
            p.emit({"type": "tool", "name": name, "text": "Building the enclosure in FreeCAD"})
            p.set_status("running", "Building the enclosure in FreeCAD")
            res = pipeline.run_enclosure(p, p.state.get("spec") or {}, args.get("enclosure") or {})
            return json.dumps(res)
        if name == "finish":
            if p.state["steps"].get("enclosure", {}).get("status") == "pending":
                p.step("enclosure", "done", "No enclosure")
            if p.state["steps"].get("review", {}).get("status") == "running":
                p.step("review", "done", "No review reported")
            p.state["finished"] = True
            p.state["final_summary"] = str(args["summary"])
            p.save()
            p.emit({"type": "finish", "text": str(args["summary"])})
            return "Finished. The user can see the summary, files and quote."
        return f"Unknown tool {name}"

    def _count(self, usage: dict) -> None:
        """Add one turn's tokens and price to the project's running total."""
        if not usage:
            return
        from .providers import turn_cost
        p = self.p
        cost = turn_cost(usage.get("model", ""), usage, config.load()["ai"])
        with p.lock:
            u = p.state.setdefault("usage", {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                                             "turns": 0, "cost": 0.0, "priced": True})
            for k in ("input", "output", "cache_read", "cache_write"):
                u[k] = u.get(k, 0) + int(usage.get(k) or 0)
            u["turns"] = u.get("turns", 0) + 1
            if cost is None:
                u["priced"] = False
            else:
                u["cost"] = round(u.get("cost", 0.0) + cost, 6)
            u["model"] = usage.get("model")
            p.save()
            snapshot = dict(u)
        p.emit({"type": "usage", **snapshot})

    def _vision(self) -> bool:
        ai = config.load()["ai"]
        mode = ai.get("vision", "auto")
        if mode in ("on", "off"):
            return mode == "on"
        if getattr(self.p.session, "vision_failed", False):
            return False
        if ai.get("provider") != "openai":
            return True
        return bool(VISION_MODELS.search(ai.get("openai_model") or ""))

    def _review_images(self) -> list[dict]:
        import base64
        folder = Path((self.p.state.get("artifacts") or {}).get("folder") or "")
        out = []
        for view in ("iso", "top", "bottom"):
            f = folder / f"render-{view}.png"
            if f.exists() and f.stat().st_size < 4_500_000:
                out.append({"media_type": "image/png", "label": f"{view} render",
                            "data": base64.b64encode(f.read_bytes()).decode()})
        return out

    def _brief_done(self) -> None:
        if self.p.state["steps"]["brief"]["status"] in ("pending", "running"):
            self.p.step("brief", "done", "Requirements understood")

    def _ask(self, question: str, options: list[str]) -> str:
        p = self.p
        qid = uuid.uuid4().hex[:8]
        p.emit({"type": "question", "id": qid, "question": question, "options": options})
        p.set_status("waiting", "Waiting for your answer")
        with p.answer_ready:
            while qid not in p.answers:
                if p.cancel.is_set():
                    raise pipeline.Cancelled()
                p.answer_ready.wait(timeout=0.5)
            answer = p.answers.pop(qid)
        p.set_status("running", "Working")
        return f"The user answered: {answer}"


def answer(project: Project, qid: str, text: str) -> None:
    with project.answer_ready:
        project.answers[qid] = text
        project.answer_ready.notify_all()
    project.emit({"type": "answer", "id": qid, "text": text})
