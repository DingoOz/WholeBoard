"""Writes the reference pages that come from the code, so they cannot drift from it.

Run by mkdocs-gen-files on every `mkdocs build` or `mkdocs serve`; the pages exist only in the built site.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import mkdocs_gen_files

from wholeboard import blocks, config, impedance, mains, mcp
from wholeboard.ai import agent
from wholeboard.jlc import fabs

ROOT = Path(__file__).resolve().parent.parent
NOTE = ("!!! info \"Generated from the code\"\n    This page is written from `{src}` each time the site is built. "
        "To change it, change the code.\n\n")


def cell(v) -> str:
    if v is None or v == "":
        return "–"
    return str(v).replace("|", "\\|").replace("\n", " ")


def table(head: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def write(path: str, text: str, src: str) -> None:
    with mkdocs_gen_files.open(path, "w") as f:
        f.write(text)
    mkdocs_gen_files.set_edit_path(path, src)


def text_block(s: str) -> str:
    return "```text\n" + s.strip("\n") + "\n```\n"


# Building blocks ---------------------------------------------------------------------------------------------

def blocks_page() -> str:
    out = ["# Building blocks\n", NOTE.format(src="wholeboard/blocks.py"),
           "Proven circuits the designer starts from. Each has real LCSC parts and named ports; a design connects "
           "the ports to its own nets and the block's parts are renumbered and merged in. See "
           "[Add your own building block](../how-to/add-block.md) for the format.\n"]
    cats: dict[str, list[dict]] = {}
    for b in blocks.BUILTIN:
        cats.setdefault(b["category"], []).append(b)
    out.append("## At a glance\n")
    out.append(table(["Block", "Category", "What it is"],
                     [[f"[`{b['id']}`](#{b['id']})", b["category"], b["title"]] for b in blocks.BUILTIN]))
    for cat, bs in cats.items():
        out.append(f"\n## {cat.capitalize()}\n")
        for b in bs:
            out.append(f"\n### `{b['id']}` {{#{b['id']}}}\n\n**{b['title']}**\n\n{b['description']}\n")
            ports = [[f"`{k}`", v.get("desc"), "power" if v.get("power") else "", "yes" if v.get("required") else "",
                      v.get("default")] for k, v in b["ports"].items()]
            out.append("\n" + table(["Port", "What it is", "Kind", "Required", "Default net"], ports))
            if b.get("options"):
                out.append("\nOptions:\n\n" + table(["Option", "Default", "What it does"],
                           [[f"`{k}`", json.dumps(v.get("default")), v.get("desc")] for k, v in b["options"].items()]))
            parts = [[c["ref"], c["value"], c.get("lcsc"), c.get("description"), f"`{c['footprint']}`"]
                     for c in b["components"]]
            out.append("\n??? note \"Parts\"\n\n" + "\n".join("    " + l for l in
                       table(["Ref", "Value", "LCSC", "Description", "Footprint"], parts).splitlines()) + "\n")
            if b.get("impedance"):
                out.append("\nInterface limits: " + "; ".join(
                    f"{', '.join(g['nets'])} as {g['kind']}" + (f" ([`{g['profile']}`](profiles.md))" if g.get("profile") else "")
                    for g in b["impedance"]) + ".\n")
    return "\n".join(out)


# Interface profiles ------------------------------------------------------------------------------------------

def profiles_page() -> str:
    out = ["# Interface profiles\n", NOTE.format(src="wholeboard/impedance.py"),
           "An `impedance` entry in the design can name a profile; the profile supplies the interface's limits and "
           "the entry's own fields override them. The post-routing check reads them.\n",
           table(["Profile", "Interface", "Kind", "Ω", "Max length (mm)", "Max skew in pair (mm)",
                  "Max match across bus (mm)", "Max vias", "Max stub (mm)"],
                 [[f"`{k}`", p["title"], p["kind"], p.get("ohms"), p.get("max_length_mm"), p.get("max_skew_mm"),
                   p.get("max_match_mm"), p.get("max_vias"), p.get("max_stub_mm")] for k, p in impedance.PROFILES.items()]),
           "\n## Stack-ups\n\nTrace widths are worked out for the outer layer over the nearest plane:\n\n",
           table(["Layers", "Stack-up", "Dielectric height (mm)", "εr"],
                 [[n, s["name"], s["h"] if s["h"] is not None else "board thickness", s["er"]]
                  for n, s in impedance.STACKUPS.items()]),
           "\n## What the designer is told\n\n", text_block(impedance.__doc__.split("\n\n", 1)[1].replace(
               "{PROFILES}", ", ".join(impedance.PROFILES)))]
    return "\n".join(out)


# Mains -------------------------------------------------------------------------------------------------------

def mains_page() -> str:
    rows = []
    for v in (120, 230, 240, 277, 400):
        for ovc in (2, 3):
            r = mains.requirements(mains.config({"mains": {"nets": ["L"], "volts": v, "overvoltage_category": ovc}}))
            rows.append([f"{v} V", "II" if ovc == 2 else "III", r["peak_v"], r["impulse_v"], r["functional_outer_mm"],
                         r["functional_inner_mm"], r["clearance_mm"], r["basic_creepage_mm"], r["creepage_mm"]])
    out = ["# Mains requirements\n", NOTE.format(src="wholeboard/mains.py"),
           "!!! danger \"An aid, not a certification\"\n    These checks cover spacing on the board, the fuse and the "
           "isolation barrier. Have a mains design reviewed, and build it into an enclosure that keeps fingers out.\n",
           "## Spacing for common voltages\n\nPollution degree 2, FR-4 (material group IIIb). Overvoltage category II "
           "is the default for plug-in equipment; set `overvoltage_category: 3` for fixed wiring. Where the figures "
           "come from is on [Standards and sources](../standards.md).\n\n",
           table(["Working voltage", "Overvoltage category", "Peak (V)", "Impulse (V)",
                  "Between mains nets, outer (mm)", "Between mains nets, inner (mm)", "Reinforced clearance (mm)",
                  "Basic creepage (mm)", "Reinforced creepage (mm)"], rows),
           f"\nSlots cut to lengthen creepage are {mains.SLOT_W:g} mm wide. Measurements are compared with a "
           f"tolerance of {mains.TOL:g} mm.\n",
           "\n## How parts are recognised\n\nWholeBoard matches these patterns (case-insensitive) against the part's value, "
           "part number, footprint and description (a fuse's reference counts too). `\"barrier\"` on a part overrides the match.\n",
           *[f"\n**{what}**\n\n```text\n{pat}\n```\n" for what, pat in (
               ("Isolation barrier", mains.BARRIER.pattern + "\n(also any T or K reference, and any part with \"barrier\": true)"),
               ("Fuse", mains.FUSE.pattern),
               ("A net name that looks like mains", mains.MAINS_NAME.pattern),
               ("A part that suggests mains", mains.MAINS_PART.pattern))],
           "\n## KiCad rules written for a mains design\n\nFor a design with `mains` declared as "
           "`{\"nets\": [\"AC_L\", \"AC_N\"], \"volts\": 230}`, the `.kicad_dru` gets:\n\n",
           "```lisp\n" + "\n".join(mains.dru_rules({"mains": {"nets": ["AC_L", "AC_N"], "volts": 230},
                                                     "nets": [], "components": []})) + "\n```\n",
           "\n## What the designer is told\n\n", text_block(mains.__doc__)]
    return "\n".join(out)


# MCP tools ---------------------------------------------------------------------------------------------------

def mcp_page() -> str:
    out = ["# MCP tools\n", NOTE.format(src="wholeboard/mcp.py"),
           "`python -m wholeboard.mcp` serves these tools over stdio (newline-delimited JSON-RPC; protocol versions "
           + ", ".join(f"`{p}`" for p in mcp.PROTOCOLS) + "). See [Use the checks from other agents](../how-to/mcp.md).\n"]
    for t in mcp.TOOLS:
        props = t["inputSchema"]["properties"]
        req = set(t["inputSchema"].get("required") or [])
        out.append(f"\n## `{t['name']}`\n\n{t['description']}\n\n" + table(
            ["Argument", "Type", "Required", "Default", "What it is"],
            [[f"`{k}`", v.get("type"), "yes" if k in req else "", json.dumps(v["default"]) if "default" in v else "",
              (v.get("description") or "")[:240] + ("…" if len(v.get("description") or "") > 240 else "")]
             for k, v in props.items()]))
    return "\n".join(out)


# JLCPCB limits and other fabs --------------------------------------------------------------------------------

def jlc_page() -> str:
    cap = json.loads((ROOT / "wholeboard/jlc/capabilities.json").read_text())
    keys = sorted({k for v in cap["layers"].values() for k in v})
    out = ["# JLCPCB limits and other fabs\n", NOTE.format(src="wholeboard/jlc/capabilities.json and wholeboard/jlc/fabs.py"),
           f"{cap['_about']}\n\nLast checked: **{cap.get('last_checked', '–')}**. Sources: "
           + ", ".join(f"<{s}>" for s in cap.get("sources", [])) + "\n",
           "\n## By layer count (mm)\n\n",
           table(["Limit"] + [f"{n} layers" for n in cap["layers"]],
                 [[k.replace("_", " ")] + [v.get(k) for v in cap["layers"].values()] for k in keys]),
           "\n## Any layer count\n\n", table(["Limit", "Value"], [[k.replace("_", " "), v] for k, v in cap["any"].items()])]
    rest = {k: v for k, v in cap.items() if k not in ("_about", "last_checked", "sources", "layers", "any")}
    if rest:
        out.append("\n## Other entries\n\n```json\n" + json.dumps(rest, indent=2) + "\n```\n")
    out += ["\n## Other fabs\n\nPrices WholeBoard works out itself (the JLCPCB quote uses "
            "[`pricing.json`](https://github.com/DingoOz/WholeBoard/blob/main/wholeboard/jlc/pricing.json)):\n\n",
            table(["Fab", "Figure", "Value"], [
                *[["OSH Park", f"{n} layers, per square inch for three boards", f"US${v:g}"] for n, v in fabs.OSHPARK.items()],
                *[["Aisler", f"{n} layers: job fee + per cm² per board", f"€{a:g} + €{b:g}"] for n, (a, b) in fabs.AISLER.items()],
                ["Aisler", "Stencil: fee + per cm²", f"€{fabs.AISLER_STENCIL[0]:g} + €{fabs.AISLER_STENCIL[1]:g}"],
                *[["Aisler", f"Small-run assembly: {k.replace('_', ' ')}", f"€{v:g}"] for k, v in fabs.AISLER_SMALL.items()],
                *[["Aisler", f"Series assembly: {k.replace('_', ' ')}", f"€{v:g}"] for k, v in fabs.AISLER_SERIES.items()],
                ["–", "Euro to US dollar", f"{fabs.EUR_USD:g}"]])]
    return "\n".join(out)


# Settings ----------------------------------------------------------------------------------------------------

def settings_page() -> str:
    src = (ROOT / "wholeboard/config.py").read_text()
    m = re.search(r"^DEFAULTS: dict = (\{.*?^\})", src, re.S | re.M)
    secrets = ", ".join(f"`{a}.{b}`" for a, b in config.SECRETS)
    return "\n".join([
        "# Settings\n", NOTE.format(src="wholeboard/config.py"),
        "Settings live in `~/.wholeboard/config.json` (or `$WHOLEBOARD_HOME/config.json`) and are edited in "
        "*Settings* in the interface. The file holds only what differs from these defaults; anything missing "
        "takes the default.\n",
        f"Secrets ({secrets}) are never sent back to the browser: the interface sees them masked. On Linux and "
        "macOS the file is readable by your user only. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `XAI_API_KEY` "
        "in the environment are used when no key is set.\n",
        "## Defaults\n", "```python\n" + (m.group(1) if m else json.dumps(config.DEFAULTS, indent=4)) + "\n```\n"])


# Design spec -------------------------------------------------------------------------------------------------

def spec_page() -> str:
    s = agent.SYSTEM
    start = s.index("Design spec (the `spec` argument of submit_design)")
    end = s.index("Common JLCPCB basic parts")
    body = s[start:end].split("\n", 1)[1]
    js_end = body.index("\n}\n") + 3
    example, rest = body[:js_end], body[js_end:]
    heads = ("Building blocks", "Controlled impedance", "Mains")
    parts, cur, title = [], [], "Fields"
    for line in rest.splitlines():
        if line.strip() in heads or line.startswith("Simulation (optional"):
            parts.append((title, "\n".join(cur)))
            title, cur = (line.split(" (")[0] if line.startswith("Simulation") else line.strip()), []
        else:
            cur.append(line)
    parts.append((title, "\n".join(cur)))
    out = ["# Design spec\n", NOTE.format(src="wholeboard/ai/agent.py and the modules it includes"),
           "The design is one JSON document: the designer AI writes it, you can edit it with *Edit design JSON*, "
           "and every build starts from it. This page is the same text the designer is given, so it is exactly what "
           "WholeBoard accepts.\n", "## Example\n", "```json\n" + example.strip() + "\n```\n"]
    for title, text in parts:
        if text.strip():
            out += [f"## {title}\n", text_block(text)]
    out.append("## Defaults for `rules`\n\n```json\n" + json.dumps(__import__("wholeboard.design").design.DEFAULT_RULES,
                                                                     indent=2) + "\n```\n")
    return "\n".join(out)


# Python API --------------------------------------------------------------------------------------------------

def api_pages() -> None:
    mods = sorted(p for p in (ROOT / "wholeboard").rglob("*.py")
                  if not p.name.startswith("_") and "web" not in p.parts)
    index = ["# Python API\n", NOTE.format(src="the module docstrings"),
             "Module and function documentation, from the docstrings. Only documented functions are shown.\n",
             "| Module | Summary |", "|---|---|"]
    for p in mods:
        parts = list(p.relative_to(ROOT).with_suffix("").parts)
        name = ".".join(parts)
        src = p.read_text().lstrip()
        summary = src.split('"""')[1].strip().splitlines()[0] if src.startswith('"""') else ""
        page = f"api/{name}.md"
        write(page, f"# `{name}`\n\n::: {name}\n    options:\n      show_root_heading: false\n", str(p.relative_to(ROOT)))
        index.append(f"| [`{name}`]({name}.md) | {cell(summary)} |")
    write("api/index.md", "\n".join(index) + "\n", "docs/gen.py")


write("reference/blocks.md", blocks_page(), "wholeboard/blocks.py")
write("reference/profiles.md", profiles_page(), "wholeboard/impedance.py")
write("reference/mains.md", mains_page(), "wholeboard/mains.py")
write("reference/mcp-tools.md", mcp_page(), "wholeboard/mcp.py")
write("reference/jlc-limits.md", jlc_page(), "wholeboard/jlc/capabilities.json")
write("reference/settings.md", settings_page(), "wholeboard/config.py")
write("reference/spec.md", spec_page(), "wholeboard/ai/agent.py")
api_pages()
