"""WholeBoard's checks as an MCP server, for any agent (Claude Code, Codex, Backplane) and any KiCad project.

    python -m wholeboard.mcp            # speaks MCP over stdio

Add it to Claude Code with:  claude mcp add wholeboard -- python -m wholeboard.mcp
(run from the WholeBoard folder, or give the full path to its Python).

Every tool takes "project": a KiCad project folder, or any .kicad_pro, .kicad_sch or .kicad_pcb file in it. The
project is read with WholeBoard's KiCad importer (the schematic's netlist, the board's outline, layers and copper),
so the checks work on boards WholeBoard did not design. Results are JSON text. kicad-cli is needed to read the
schematic, ngspice to simulate, and the internet for JLCPCB prices and stock.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sys
import threading
import traceback
from pathlib import Path

from . import bringup, checks, config, design, impedance, mains, ngspice, power, sim
from .jlc import parts as jparts, quote as jquote, stock as jstock
from .kicad import geometry, importer

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
WORK = Path.home() / ".wholeboard" / "mcp"
_cache: dict = {}
_lock = threading.Lock()

PROJECT = {"type": "string", "description": "A KiCad project folder, or a .kicad_pro, .kicad_sch or .kicad_pcb file in it."}
TOOLS = [
    {"name": "simulate",
     "description": "Simulate the circuit with ngspice: supply currents, rail voltages, oscillator frequencies, and "
                    "problems such as a regulator over its limit, a part over its voltage or power rating, or an LED "
                    "over its current. Models come from the parts' values and LCSC data.",
     "inputSchema": {"type": "object", "properties": {"project": PROJECT}, "required": ["project"]}},
    {"name": "power_budget",
     "description": "Power budget and heat: current drawn from each supply against its limit, each regulator's load, "
                    "dissipation and temperature rise, and battery life if there is a battery. Runs the simulation.",
     "inputSchema": {"type": "object", "properties": {"project": PROJECT}, "required": ["project"]}},
    {"name": "impedance_check",
     "description": "Controlled-impedance and interface routing check on the routed board: track width for the "
                    "target impedance on JLCPCB's stack-up, pair skew, length, vias and stubs against interface "
                    "profiles (usb2_hs, usb2_fs, ethernet_100, rmii, qspi_flash, rf_50). Give groups, or leave them "
                    "out to check every differential pair found by name (USB_DP/USB_DN, TX+/TX-).",
     "inputSchema": {"type": "object", "properties": {
         "project": PROJECT,
         "groups": {"type": "array", "description": impedance.DOC[:600], "items": {"type": "object"}}},
         "required": ["project"]}},
    {"name": "board_checks",
     "description": "Checks on the routed board: track temperature rise for the simulated currents (IPC-2152 and "
                    "IPC-2221), voltage drop along supply tracks, decoupling capacitors' distance from their IC, and "
                    "mains creepage and clearance when the project has mains nets (give mains_nets).",
     "inputSchema": {"type": "object", "properties": {
         "project": PROJECT,
         "mains_nets": {"type": "array", "items": {"type": "string"}, "description": "Nets at mains voltage, e.g. [\"AC_L\", \"AC_N\"]."},
         "mains_volts": {"type": "number", "description": "Mains working voltage, rms (default 250)."}},
         "required": ["project"]}},
    {"name": "jlc_quote",
     "description": "Price the board at JLCPCB: fabrication, assembly, parts at live LCSC prices, shipping, and the "
                    "features that cost extra (small vias, vias in pads, bottom-side parts). Parts need LCSC numbers "
                    "in a field called LCSC.",
     "inputSchema": {"type": "object", "properties": {
         "project": PROJECT, "quantity": {"type": "integer", "minimum": 1, "default": 5},
         "assembly": {"type": "boolean", "default": True},
         "assembly_quantity": {"type": "integer", "minimum": 1, "default": 2}}, "required": ["project"]}},
    {"name": "stock_check",
     "description": "Live JLCPCB stock for every BOM line, for the boards to be assembled, with in-stock "
                    "replacements of the same value and package for parts that are short.",
     "inputSchema": {"type": "object", "properties": {
         "project": PROJECT, "assembly_quantity": {"type": "integer", "minimum": 1, "default": 2}},
         "required": ["project"]}},
    {"name": "bringup_guide",
     "description": "A bring-up checklist for when the boards arrive, as Markdown: inspection, first power-up with "
                    "a current limit from the simulated draw, the voltage and current to expect on each rail, and "
                    "the signals to look for.",
     "inputSchema": {"type": "object", "properties": {"project": PROJECT}, "required": ["project"]}},
]


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _folder(project: str) -> Path:
    p = Path(project).expanduser().resolve()
    if p.is_file():
        p = p.parent
    if not p.is_dir():
        raise ValueError(f"{project} is not a folder or a file in one")
    return p


def load(project: str) -> dict:
    """The imported project: {"spec", "notes", "errors", "warnings", "files", "folder", "pcb", "work"}."""
    folder = _folder(project)
    stamp = tuple(sorted((f.name, f.stat().st_mtime_ns) for f in folder.iterdir()
                         if f.suffix in (".kicad_sch", ".kicad_pcb", ".kicad_pro")))
    with _lock:
        hit = _cache.get(str(folder))
        if hit and hit[0] == stamp:
            return hit[1]
    res = importer.to_spec(folder, log=_log)
    res["folder"] = folder
    res["pcb"] = folder / res["files"]["board"] if res["files"].get("board") else None
    res["work"] = WORK / hashlib.sha1(str(folder).encode()).hexdigest()[:12]
    res["work"].mkdir(parents=True, exist_ok=True)
    with _lock:
        _cache[str(folder)] = (stamp, res)
    return res


def _geo(res: dict) -> dict:
    """The routed board's copper, with KiCad's net names turned into the imported spec's."""
    if not res.get("pcb"):
        raise ValueError("the project has no .kicad_pcb, so there is no board to check")
    geo = geometry.read(res["pcb"])
    pad_net = {(p["ref"], p["name"]): p["net"] for p in geo["pads"]}
    rename = {}
    for n in res["spec"]["nets"]:
        for pin in n["pins"]:
            raw = pad_net.get(tuple(pin.split(".", 1)))
            if raw:
                rename[raw] = n["name"]
    for key in ("pads", "tracks", "arcs", "vias", "zones"):
        for item in geo.get(key) or []:
            if item.get("net") in rename:
                item["net"] = rename[item["net"]]
    return geo


def _sim(res: dict) -> dict:
    cfg = config.load()

    def lookup(code):
        try:
            return jparts.lookup(code)
        except Exception:  # noqa: BLE001
            return None
    return sim.simulate(design.normalise(res["spec"]), res["work"] / "sim", cfg, ngspice.find(cfg), part_info=lookup, log=_log)


def _sim_brief(r: dict) -> dict:
    return {k: r.get(k) for k in ("ran", "ok", "skipped", "problems", "warnings", "supplies", "rails", "signals",
                                  "net_current_ma", "parts") if r.get(k) is not None}


def _pairs(spec: dict) -> list[dict]:
    names = [n["name"] for n in spec["nets"]]
    out, seen = [], set()
    for a in names:
        for b in names:
            if a < b and a not in seen and b not in seen and impedance.pair_names(a, b):
                up = a.upper()
                prof = "usb2_hs" if "USB" in up or up[:-1].endswith("D") else (
                    "ethernet_100" if any(k in up for k in ("ETH", "TX", "RX", "TD", "RD")) else None)
                g = {"nets": [a, b] if a[-1] in "P+" else [b, a], "kind": "diff"}
                if prof:
                    g["profile"] = prof
                else:
                    g["ohms"] = 90
                out.append(g)
                seen.update((a, b))
    return out


def call(name: str, args: dict) -> dict:
    res = load(args["project"])
    spec = res["spec"]
    head = {"project": str(res["folder"]), "files": res["files"], "import_notes": res["notes"],
            "import_errors": res["errors"][:20]}
    if name == "simulate":
        return {**head, **_sim_brief(_sim(res))}
    if name == "power_budget":
        r = _sim(res)
        if not r.get("ran"):
            return {**head, "error": r.get("skipped") or "the simulation did not run", **_sim_brief(r)}
        return {**head, **power.brief(power.budget(design.normalise(spec), r))}
    if name == "impedance_check":
        groups = args.get("groups") or _pairs(spec)
        if not groups:
            return {**head, "groups": [], "note": "No differential pairs found by name; give groups."}
        s = {**spec, "impedance": groups}
        errs, warns = impedance.validate(s)
        if errs:
            return {**head, "errors": errs}
        return {**head, **impedance.check(design.normalise(s), _geo(res)), "validation_warnings": warns}
    if name == "board_checks":
        r = _sim(res)
        s = design.normalise(spec)
        if args.get("mains_nets"):
            s["mains"] = {"nets": args["mains_nets"], "volts": args.get("mains_volts") or mains.DEFAULT_VOLTS}
        geo = _geo(res)
        out = {**head, **checks.run(s, geo, r.get("net_current_ma"), r.get("supplies"))}
        if mains.config(s):
            m = mains.check(s, geo)
            out["mains"] = {k: m.get(k) for k in ("requirements", "problems", "barrier", "rows")}
            out["mains_design"] = dict(zip(("errors", "warnings"), mains.validate(s)))
        return out
    if name == "jlc_quote":
        opts = {**config.load()["jlcpcb"], **{k: args[k] for k in ("quantity", "assembly", "assembly_quantity") if k in args}}
        geo = _geo(res) if res.get("pcb") else None
        q = jquote.full_quote(spec, opts, log=_log, geo=geo)
        return {**head, **{k: q.get(k) for k in ("currency", "quantity", "pcb", "assembly", "components", "shipping",
                                                 "total", "per_board", "assumptions", "fab_features", "order_url")}}
    if name == "stock_check":
        opts = {**config.load()["jlcpcb"], "assembly_quantity": int(args.get("assembly_quantity") or 2)}
        return {**head, **jstock.check(spec, opts, log=_log)}
    if name == "bringup_guide":
        r = _sim(res)
        b = None
        if r.get("ran"):
            try:
                b = power.budget(design.normalise(spec), r)
            except Exception:  # noqa: BLE001
                b = None
        g = bringup.guide(design.normalise(spec), r, b, None)
        return {**head, "markdown": g["markdown"]}
    raise KeyError(name)


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message in, the reply out (None for notifications)."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None                      # notifications/initialized and friends

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    if method == "initialize":
        want = params.get("protocolVersion")
        return ok({"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "wholeboard", "version": "1.0"},
                   "instructions": __doc__.split("\n\n", 2)[2].strip()})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        try:
            # keep stray prints off stdout, which carries the protocol
            with contextlib.redirect_stdout(sys.stderr):
                out = call(name, args)
            return ok({"content": [{"type": "text", "text": json.dumps(out, indent=1, default=str)}], "isError": False})
        except Exception as e:  # noqa: BLE001
            _log(traceback.format_exc())
            return ok({"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}


def main() -> None:
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            out.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}) + "\n")
            out.flush()
            continue
        for m in msg if isinstance(msg, list) else [msg]:
            reply = handle(m)
            if reply is not None:
                out.write(json.dumps(reply) + "\n")
                out.flush()


if __name__ == "__main__":
    main()
