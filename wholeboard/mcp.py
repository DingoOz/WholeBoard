"""WholeBoard as an MCP server: its checks for any agent and any KiCad project, and its designer's tools for Claude Code.

    python -m wholeboard.mcp            # speaks MCP over stdio

Add it to Claude Code with:  claude mcp add --scope user wholeboard -- /path/to/WholeBoard/.venv/bin/python -m wholeboard.mcp
(WholeBoard's "Design with Claude Code" screen shows the line for this computer, and can run it).

Every tool takes "project": a KiCad project folder, or any .kicad_pro, .kicad_sch or .kicad_pcb file in it. The
project is read with WholeBoard's KiCad importer (the schematic's netlist, the board's outline, layers and copper),
so the checks work on boards WholeBoard did not design. Results are JSON text. kicad-cli is needed to read the
schematic, ngspice to simulate, and the internet for JLCPCB prices and stock.

To design a new board with WholeBoard, call design_guide first and follow it: new_board starts the board, and the
designer's tools (search_parts, simulate_design, submit_design, build_enclosure, finish and the rest) build it in
WholeBoard, where the user watches it live. These need WholeBoard running on this computer (./run.sh).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
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


# ---- designing a board: the tools run in the WholeBoard server, so its page follows the build live ----------------

BOARD = {"type": "string", "description": "The board's id (default: the board from new_board or open_board)."}
_board: dict = {"id": None}
_out_lock = threading.Lock()
_STDOUT = sys.stdout


def _design_tools() -> list[dict]:
    from .ai import agent
    tools = [
        {"name": "design_guide",
         "description": "How to design a board with WholeBoard: the workflow, engineering rules and the design spec "
                        "format. Read it before new_board.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "new_board",
         "description": "Start a new board in WholeBoard from the user's request. Returns its id and the link where "
                        "the user watches it being designed and built. The other design tools then work on it.",
         "inputSchema": {"type": "object", "properties": {"request": {"type": "string", "description":
                         "The user's description of the board, in their words."}}, "required": ["request"]}},
        {"name": "list_boards",
         "description": "The boards in WholeBoard, newest first: id, title, status and price.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "open_board",
         "description": "Carry on with an existing board: its status, steps, current design spec and enclosure. The "
                        "other design tools then work on it.",
         "inputSchema": {"type": "object", "properties": {"board": {"type": "string"}}, "required": ["board"]}},
    ]
    for t in agent.outside_tools():
        schema = t["input_schema"]
        tools.append({"name": t["name"], "description": t["description"],
                      "inputSchema": {**schema, "properties": {**schema["properties"], "board": BOARD}}})
    return tools


DESIGN_TOOLS = _design_tools()
DESIGN_NAMES = {t["name"] for t in DESIGN_TOOLS}


def _base() -> str:
    return (os.environ.get("WHOLEBOARD_URL") or f"http://127.0.0.1:{config.load().get('port', 8770)}").rstrip("/")


def _http(method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
    req = urllib.request.Request(_base() + path, method=method, headers={"content-type": "application/json"},
                                 data=None if body is None else json.dumps(body).encode())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))     # never through an HTTP proxy
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail")
        except Exception:  # noqa: BLE001
            detail = e.reason
        raise RuntimeError(f"WholeBoard said: {detail}") from None
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"WholeBoard is not running at {_base()} ({getattr(e, 'reason', e)}). Ask the user to "
                           "start it (./run.sh, or run.bat on Windows), or set WHOLEBOARD_URL.") from None


def _waiting(fn, progress):
    """Run fn in a thread, sending a progress notification every 15 s so a long build is not taken for a hang."""
    box: dict = {}
    t = threading.Thread(target=lambda: box.update(out=_try(fn)), daemon=True)
    t.start()
    start = time.time()
    while not t.join(15) and t.is_alive():
        if progress is not None:
            _send({"jsonrpc": "2.0", "method": "notifications/progress", "params": {
                "progressToken": progress, "progress": int(time.time() - start),
                "message": f"WholeBoard is still working ({int(time.time() - start)} s)"}})
    ok, val = box["out"]
    if not ok:
        raise val
    return val


def _try(fn):
    try:
        return True, fn()
    except Exception as e:  # noqa: BLE001
        return False, e


def _brief(state: dict, title: str, url: str) -> dict:
    return {"board": state["id"], "title": title, "url": url, "status": state.get("status"),
            "status_note": state.get("status_note"), "revision": state.get("revision"),
            "steps": {k: f"{v.get('status')}: {v.get('detail')}".rstrip(": ") for k, v in (state.get("steps") or {}).items()},
            "quote_total": (state.get("quote") or {}).get("total"), "finished": bool(state.get("finished")),
            "final_summary": state.get("final_summary"), "spec": state.get("spec"),
            "enclosure": state.get("enclosure_spec")}


def design_call(name: str, args: dict, progress=None) -> list[dict]:
    """A design tool: answered here (guide, boards) or run in the WholeBoard server. Returns MCP content blocks."""
    from .ai import agent
    if name == "design_guide":
        return [{"type": "text", "text": agent.guide()}]
    if name == "new_board":
        r = _http("POST", "/api/projects", {"prompt": str(args["request"]), "driver": "claude-code"})
        _board["id"] = r["id"]
        return [{"type": "text", "text": json.dumps({"board": r["id"], "url": r["url"], "note":
                 "Give the user this link: the board builds there live. Ask your questions, then search for parts."})}]
    if name == "list_boards":
        rows = [{k: b.get(k) for k in ("id", "title", "status", "total")} for b in _http("GET", "/api/projects")
                if not b.get("hidden")]
        return [{"type": "text", "text": json.dumps(rows[:40], indent=1)}]
    if name == "open_board":
        r = _http("GET", f"/api/projects/{args['board']}")
        _board["id"] = r["state"]["id"]
        return [{"type": "text", "text": json.dumps(_brief(r["state"], r["title"], f"{_base()}/#{_board['id']}"),
                                                     indent=1, default=str)}]
    pid = args.pop("board", None) or _board["id"]
    if not pid:
        raise RuntimeError("No board yet: call new_board (or open_board) first.")
    r = _waiting(lambda: _http("POST", f"/api/projects/{pid}/tool", {"name": name, "args": args}, timeout=3 * 3600),
                 progress)
    text = r.get("text") if isinstance(r.get("text"), str) else json.dumps(r.get("text"), default=str)
    if r.get("user_messages"):
        text += "\n\nuser_messages (typed in WholeBoard's chat): " + json.dumps(r["user_messages"])
    content = [{"type": "text", "text": text}]
    for img in r.get("images") or []:
        content.append({"type": "image", "data": img["data"], "mimeType": img.get("media_type", "image/png")})
    if r.get("error"):
        raise RuntimeError(text)
    return content


def _send(msg: dict) -> None:
    """Write one message to the protocol's stdout (the real one, even while a tool's prints go to stderr)."""
    with _out_lock:
        _STDOUT.write(json.dumps(msg) + "\n")
        _STDOUT.flush()


# ---------------------------------------------------------------- adding it to Claude Code (for WholeBoard's page)
def add_command(port: int | None = None) -> list[str]:
    """`claude mcp add` for this WholeBoard: its own Python, and its port when that is not the configured one."""
    argv = ["claude", "mcp", "add", "--scope", "user", "wholeboard"]
    if port and port != int(config.load().get("port", 8770)):
        argv += ["-e", f"WHOLEBOARD_URL=http://127.0.0.1:{port}"]
    return argv + ["--", sys.executable, "-m", "wholeboard.mcp"]


def shell_line(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def added() -> bool:
    """Whether Claude Code (on this computer, for this user) already has this MCP server, in any scope."""
    try:
        cfg = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    scopes = [cfg.get("mcpServers") or {}] + [p.get("mcpServers") or {} for p in (cfg.get("projects") or {}).values()
                                               if isinstance(p, dict)]
    return any("wholeboard.mcp" in (s.get("args") or []) for servers in scopes for s in servers.values()
               if isinstance(s, dict))


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
        return ok({"tools": TOOLS + DESIGN_TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS} | DESIGN_NAMES:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        try:
            # keep stray prints off stdout, which carries the protocol
            with contextlib.redirect_stdout(sys.stderr):
                if name in DESIGN_NAMES:
                    return ok({"content": design_call(name, dict(args), (params.get("_meta") or {}).get("progressToken")),
                               "isError": False})
                out = call(name, args)
            return ok({"content": [{"type": "text", "text": json.dumps(out, indent=1, default=str)}], "isError": False})
        except Exception as e:  # noqa: BLE001
            _log(traceback.format_exc())
            return ok({"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}


def main() -> None:
    global _STDOUT
    _STDOUT = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        for m in msg if isinstance(msg, list) else [msg]:
            reply = handle(m)
            if reply is not None:
                _send(reply)


if __name__ == "__main__":
    main()
