"""Write a KiCad 10 schematic (.kicad_sch) from a design spec.

Every component gets its library symbol (or a generated box symbol when the spec names none). Pins connect
through net labels, so the schematic is electrically identical to the board's netlist.

To make it readable:
- Each building block (or group named by a component's "sheet") gets a sheet of its own in a hierarchical
  schematic, with hierarchical labels for the nets it shares with the rest; the main sheet holds the other
  parts and a sheet symbol per block.
- On each sheet the parts are gathered round the IC or connector they share nets with, and the groups run in
  signal order: connectors, protection, regulators, the main parts, then indicators and controls.
- Supply nets use global labels; nets that stay on one sheet use local labels.
"""

from __future__ import annotations

import copy
import math
import re
import uuid
from pathlib import Path

from .. import design, sexpr
from ..sexpr import S, Sym, find, find_all, num, tag
from . import libs

FORMAT_VERSION = "20260306"
GRID = 2.54
_NS = uuid.UUID("6f1b8d52-8c41-4b9e-9a77-3d0f2b9e5a10")
_SYM_CACHE: dict[str, list | None] = {}
_FILE_CACHE: dict[str, str] = {}


def symbol_uuid(project: str, ref: str) -> str:
    """Stable UUID shared by a schematic symbol and its footprint's path."""
    return str(uuid.uuid5(_NS, f"{project}/{ref}"))


def uid() -> str:
    return str(uuid.uuid4())


def _snap(v: float) -> float:
    return round(v / GRID) * GRID


# ---------------------------------------------------------------- symbol library access

def _extract(text: str, name: str) -> str | None:
    """Cut one top-level symbol out of a .kicad_sym file without parsing all of it."""
    m = re.search(r'^\t\(symbol "' + re.escape(name) + r'"', text, re.M)
    if not m:
        return None
    i = m.start() + 1
    depth, in_str, esc = 0, False, False
    for j in range(i, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return None


def load_symbol(lib_id: str) -> list | None:
    """The symbol node, with any `extends` resolved, named 'Lib:Name'."""
    if lib_id in _SYM_CACHE:
        return _SYM_CACHE[lib_id]
    if ":" not in lib_id:
        return None
    lib, name = lib_id.split(":", 1)
    root = libs.locate("symbols")
    node = None
    if root:
        path = root / f"{lib}.kicad_sym"
        if path.exists():
            key = str(path)
            if key not in _FILE_CACHE:
                _FILE_CACHE[key] = path.read_text(encoding="utf-8")
            snippet = _extract(_FILE_CACHE[key], name)
            if snippet:
                node = sexpr.parse(snippet)
                ext = find(node, "extends")
                if ext:
                    parent = load_symbol(f"{lib}:{ext[1]}")
                    node = _flatten(node, parent, name, str(ext[1])) if parent else None
    if node is not None:
        node = copy.deepcopy(node)
        node[1] = lib_id
    _SYM_CACHE[lib_id] = node
    return node


def _flatten(child: list, parent: list, name: str, parent_name: str) -> list:
    out = copy.deepcopy(parent)
    out[1] = name
    props = {p[1]: p for p in find_all(child, "property")}
    for i, c in enumerate(out):
        if tag(c) == "property" and c[1] in props:
            out[i] = copy.deepcopy(props.pop(c[1]))
        elif tag(c) == "symbol" and isinstance(c[1], str):
            c[1] = c[1].replace(parent_name, name, 1)
    out.extend(props.values())
    return out


def symbol_pins(node: list) -> dict[int, list[dict]]:
    """Pins per unit (unit 0 = shared by all units)."""
    units: dict[int, list[dict]] = {}
    base = str(node[1]).split(":")[-1]
    for sub in find_all(node, "symbol"):
        m = re.match(re.escape(base) + r"_(\d+)_(\d+)$", str(sub[1]))
        if not m:
            m = re.search(r"_(\d+)_(\d+)$", str(sub[1]))
        if not m or int(m.group(2)) > 1:
            continue
        unit = int(m.group(1))
        for p in find_all(sub, "pin"):
            at = find(p, "at")
            nm, no = find(p, "name"), find(p, "number")
            units.setdefault(unit, []).append({
                "x": num(at[1]), "y": num(at[2]), "angle": num(at[3]) if len(at) > 3 else 0,
                "length": num(find(p, "length")[1]) if find(p, "length") else 2.54,
                "name": str(nm[1]) if nm else "", "number": str(no[1]) if no else "",
                "type": str(p[1]),
            })
    return units


def unit_count(node: list) -> int:
    us = [u for u in symbol_pins(node) if u > 0]
    return max(us) if us else 1


def _bbox(node: list, unit: int) -> tuple[float, float, float, float]:
    xs, ys = [], []
    base = str(node[1]).split(":")[-1]
    for sub in find_all(node, "symbol"):
        m = re.search(r"_(\d+)_(\d+)$", str(sub[1]))
        if not m or int(m.group(1)) not in (0, unit) or int(m.group(2)) > 1:
            continue
        for g in sub:
            t = tag(g)
            if t == "pin":
                at = find(g, "at")
                xs.append(num(at[1])); ys.append(num(at[2]))
            elif t in ("rectangle", "polyline", "circle", "arc", "bezier"):
                for pt in sexpr.walk(g):
                    if tag(pt) in ("start", "end", "xy", "mid", "center"):
                        xs.append(num(pt[1])); ys.append(num(pt[2]))
    _ = base
    if not xs:
        return (-2.54, -2.54, 2.54, 2.54)
    return (min(xs), min(ys), max(xs), max(ys))


def symbol_info(lib_id: str) -> dict | None:
    node = load_symbol(lib_id)
    if node is None:
        return None
    props = {p[1]: str(p[2]) for p in find_all(node, "property")}
    pins = []
    for unit, plist in sorted(symbol_pins(node).items()):
        for p in plist:
            pins.append({"number": p["number"], "name": p["name"], "type": p["type"], "unit": unit})
    return {"symbol": lib_id, "description": props.get("Description", ""),
            "default_footprint": props.get("Footprint", ""), "units": unit_count(node),
            "pins": pins}


# ---------------------------------------------------------------- generated box symbols

def generic_symbol(lib_id: str, pads: list[str], value: str) -> list:
    """A rectangle with one pin per pad, pins split between the left and right sides."""
    n = len(pads)
    left = pads[: math.ceil(n / 2)]
    right = pads[math.ceil(n / 2):]
    rows = max(len(left), len(right), 1)
    longest = max([len(p) for p in pads] + [2])
    half_w = _snap(max(5.08, longest * 1.6 + 2.54))
    top = _snap((rows - 1) * GRID / 2 + GRID)
    body = [Sym("symbol"), lib_id.split(":")[-1] + "_0_1",
            [Sym("rectangle"), S(Sym("start"), -half_w, top), S(Sym("end"), half_w, -top),
             [Sym("stroke"), S(Sym("width"), 0.254), S(Sym("type"), Sym("default"))],
             [Sym("fill"), S(Sym("type"), Sym("background"))]]]
    pins = [Sym("symbol"), lib_id.split(":")[-1] + "_1_1"]
    for side, plist in ((-1, left), (1, right)):
        for i, pad in enumerate(plist):
            y = top - GRID - i * GRID
            x = side * (half_w + GRID)
            pins.append([Sym("pin"), Sym("passive"), Sym("line"),
                         S(Sym("at"), x, y, 0 if side < 0 else 180), S(Sym("length"), GRID),
                         [Sym("name"), pad, [Sym("effects"), [Sym("font"), S(Sym("size"), 1.27, 1.27)]]],
                         [Sym("number"), pad, [Sym("effects"), [Sym("font"), S(Sym("size"), 1.27, 1.27)]]]])
    node = [Sym("symbol"), lib_id, S(Sym("pin_names"), S(Sym("offset"), 1.016)),
            S(Sym("exclude_from_sim"), Sym("no")), S(Sym("in_bom"), Sym("yes")),
            S(Sym("on_board"), Sym("yes"))]
    for key, val, y in (("Reference", "U", top + 1.27), ("Value", value, -top - 1.27)):
        node.append([Sym("property"), key, val, S(Sym("at"), 0, y, 0),
                     [Sym("effects"), [Sym("font"), S(Sym("size"), 1.27, 1.27)]]])
    for key in ("Footprint", "Datasheet", "Description"):
        node.append([Sym("property"), key, "", S(Sym("at"), 0, 0, 0),
                     [Sym("effects"), [Sym("font"), S(Sym("size"), 1.27, 1.27)], S(Sym("hide"), Sym("yes"))]])
    node += [body, pins, S(Sym("embedded_fonts"), Sym("no"))]
    return node


# ---------------------------------------------------------------- schematic writer

PAPERS = [("A4", 297, 210), ("A3", 420, 297), ("A2", 594, 420), ("A1", 841, 594), ("A0", 1189, 841)]
STAGES = ("connectors", "protection", "power", "main parts", "indicators and controls")
_ANCHOR = re.compile(r"^(U|IC|MOD|J|P|CN|USB|A)\d")


def stage(c: dict) -> int:
    """Where a part sits in the signal order: connector, protection, regulator, load."""
    ref = str(c.get("ref") or "")
    prefix = (re.match(r"[A-Z]+", ref) or re.match("", "")).group(0)
    fp, sym = str(c.get("footprint") or "").lower(), str(c.get("symbol") or "").lower()
    blob = " ".join(str(c.get(k) or "") for k in ("value", "description", "mpn", "symbol")).lower()
    if prefix in ("J", "P", "CN", "USB") or fp.startswith("connector"):
        return 0
    if prefix in ("F", "FB") or re.search(r"\b(tvs|esd|usblc|fuse|polyfuse|ptc|reverse|protect)", blob):
        return 1
    if prefix in ("U", "IC", "PS") and (sym.startswith(("regulator_", "power_management")) or re.search(
            r"\b(ldo|regulator|buck|boost|converter|ams1117|ap2112|xc6206|me6211|tps5|tps6|mp1584|ch224|charger)", blob)):
        return 2
    if prefix in ("U", "IC", "MOD", "A", "Y", "X"):
        return 3
    return 4


def _groups(items: list[dict], pin_net: dict, power: set[str]) -> list[list[dict]]:
    """Gather the parts round the ICs and connectors they share nets with; the IC or connector comes first."""
    refs = list(dict.fromkeys(it["c"]["ref"] for it in items))
    nets_of: dict[str, set[str]] = {r: set() for r in refs}
    for pin, net in pin_net.items():
        r = pin.split(".", 1)[0]
        if r in nets_of:
            nets_of[r].add(net)
    comp = {it["c"]["ref"]: it["c"] for it in items}
    anchors = [r for r in refs if _ANCHOR.match(r)]
    owner = {r: r for r in anchors}

    def score(r: str, a: str) -> tuple:
        sig = len((nets_of[r] & nets_of[a]) - power)
        pwr = len((nets_of[r] & nets_of[a]) - {"GND"})
        return (sig, pwr, stage(comp[a]) == 3)
    for r in refs:
        if r in owner:
            continue
        best = max(anchors, key=lambda a: score(r, a), default=None)
        if best and score(r, best)[0]:
            owner[r] = best
    # parts joined only through other parts (an LED behind its resistor) follow them
    for _ in range(4):
        for r in refs:
            if r in owner:
                continue
            for o in refs:
                if o in owner and (nets_of[r] & nets_of[o]) - power:
                    owner[r] = owner[o]
                    break
    # what is left goes with the IC whose supply it shares (decoupling capacitors), or stays on its own
    for r in refs:
        if r not in owner:
            best = max(anchors, key=lambda a: score(r, a), default=None)
            owner[r] = best if best and score(r, best)[1] else r
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(owner[it["c"]["ref"]], []).append(it)
    for lead, g in groups.items():
        g.sort(key=lambda it: (it["c"]["ref"] != lead, stage(it["c"]), it["c"]["ref"], it["unit"]))
    return sorted(groups.values(), key=lambda g: (stage(g[0]["c"]), -len(g), g[0]["c"]["ref"]))


def _arrange(items: list[dict], pin_net: dict, power: set[str], side: list[tuple[float, float]] = ()) -> tuple:
    """Lay out one sheet: groups in columns by signal order, each group's parts beside its IC or connector.

    side: extra boxes (w, h), such as sheet symbols, put in a column of their own on the right.
    Returns (positions per item, positions per side box, paper, paper width, paper height)."""
    pos: dict[int, tuple[float, float]] = {}
    blocks = []                                     # (stage, w, h, [(item index, dx, dy)])
    index = {id(it): i for i, it in enumerate(items)}
    for g in _groups(items, pin_net, power):
        lead, rest = g[0], g[1:]
        # columns of parts beside the lead part, about as tall as the group is wide
        cap = max(lead["h"], 40.0, math.sqrt(sum(it["w"] * it["h"] for it in rest) * 1.2))
        col, cx, cy, colw = [], lead["w"], 0.0, 0.0
        for it in rest:
            if cy + it["h"] > cap and col:
                cx += colw
                cy, colw = 0.0, 0.0
            col.append((index[id(it)], cx, cy))
            cy += it["h"]
            colw = max(colw, it["w"])
        w = cx + colw if rest else lead["w"]
        h = max([lead["h"]] + [dy + items[i]["h"] for i, _, dy in col])
        blocks.append((stage(lead["c"]), w, h, [(index[id(lead)], 0.0, 0.0)] + col))
    # the groups run down the columns in signal order; a column is as tall as an A-paper-shaped sheet allows
    limit = max([b[2] for b in blocks] + [math.sqrt(sum((b[1] + 10) * (b[2] + 6) for b in blocks) / 1.4)])
    x = y = colw = width = height = 0.0
    for _, w, h, members in blocks:
        if y + h > limit and y > 0:
            x += colw + 12
            y, colw = 0.0, 0.0
        for i, dx, dy in members:
            pos[i] = (x + dx, y + dy)
        y += h + 6
        colw = max(colw, w)
        height = max(height, y)
        width = max(width, x + colw + 12)
    side_pos, y = [], 0.0
    for w, h in side:
        side_pos.append((width + 10, y))
        y += h + 12
        height = max(height, y)
    if side:
        width += 10 + max(w for w, _ in side)
    for paper, pw, ph in PAPERS:
        if width <= pw - 40 and height <= ph - 40:
            break
    else:
        paper, pw, ph = "User", max(1600.0, width + 40), max(1200.0, height + 40)
    return [pos[i] for i in range(len(items))], side_pos, paper, pw, ph


def sheets(spec: dict) -> list[dict]:
    """The schematic's sheets: the main one, then one per building block (or per component "sheet" name)."""
    spec = design.normalise(spec)
    name = spec["name"]
    out = [{"name": "", "title": "Main", "refs": [], "file": f"{name}.kicad_sch", "path": "/", "uuid": None}]
    by = {}
    for c in spec["components"]:
        group = c.get("sheet") or c.get("block")
        if not group:
            out[0]["refs"].append(c["ref"])
            continue
        group = re.sub(r"[^A-Za-z0-9_]+", "_", str(group)).strip("_") or "sheet"
        if group not in by:
            bid = next((b.get("block") for b in spec.get("blocks_used") or [] if b.get("name") == group), None)
            sid = symbol_uuid(name, f"#sheet/{group}")
            by[group] = {"name": group, "title": f"{group} ({bid})" if bid else group, "refs": [],
                         "file": f"{name}-{group}.kicad_sch", "uuid": sid, "path": f"/{sid}"}
            out.append(by[group])
        by[group]["refs"].append(c["ref"])
    return out


def board_links(spec: dict) -> dict[str, tuple[str, str, str]]:
    """ref -> (footprint path, sheet name, sheet file), linking each footprint to its schematic symbol."""
    name = design.normalise(spec)["name"]
    out = {}
    for sh in sheets(spec):
        for ref in sh["refs"]:
            path = (f"/{sh['uuid']}" if sh["uuid"] else "") + f"/{symbol_uuid(name, ref)}"
            out[ref] = (path, f"/{sh['name']}/" if sh["name"] else "/", sh["file"])
    return out


def _header(uuid_: str, paper: str, pw: float, ph: float, title: str, lib_symbols: list) -> list:
    return [Sym("kicad_sch"), S(Sym("version"), Sym(FORMAT_VERSION)), S(Sym("generator"), "eeschema"),
            S(Sym("generator_version"), "10.0"), [Sym("uuid"), uuid_],
            S(Sym("paper"), paper) if paper != "User" else S(Sym("paper"), "User", pw, ph),
            [Sym("title_block"), [Sym("title"), title[:80]], [Sym("company"), "Generated by WholeBoard"]],
            [Sym("lib_symbols"), *lib_symbols]]


def _label(kind: str, net: str, x: float, y: float, ang: float) -> list:
    just = "left" if ang in (0, 90) else "right"
    eff = [Sym("effects"), [Sym("font"), S(Sym("size"), 1.27, 1.27)], S(Sym("justify"), Sym(just))]
    if kind == "label":
        return [Sym("label"), net, S(Sym("at"), x, y, ang), S(Sym("fields_autoplaced"), Sym("yes")), eff,
                [Sym("uuid"), uid()]]
    return [Sym(kind), net, S(Sym("shape"), Sym("passive")), S(Sym("at"), x, y, ang),
            S(Sym("fields_autoplaced"), Sym("yes")), eff, [Sym("uuid"), uid()]]


def _symbols(out: list, items: list[dict], placed: list, name: str, inst_path: str, pin_net: dict,
             label_kind) -> None:
    """Append the placed symbol instances, their labels and no-connect flags."""
    for it, (ox, oy) in zip(items, placed):
        c, node, unit = it["c"], it["node"], it["unit"]
        x0, y0, x1, y1 = it["bbox"]
        # symbol origin so that its bbox (y up) lands at the packed spot (y down)
        X = _snap(20 + ox + it["rx"] - x0)
        Y = _snap(20 + oy + it["ry"] + (0 if it["vertical"] else 3) + y1)
        ref = c["ref"]
        sid = symbol_uuid(name, ref) if unit == 1 else symbol_uuid(name, f"{ref}#{unit}")
        inst = [Sym("symbol"), S(Sym("lib_id"), str(node[1])), S(Sym("at"), X, Y, 0),
                S(Sym("unit"), unit), S(Sym("body_style"), 1),
                S(Sym("exclude_from_sim"), Sym("no")), S(Sym("in_bom"), Sym("yes")),
                S(Sym("on_board"), Sym("yes")), S(Sym("in_pos_files"), Sym("yes")),
                S(Sym("dnp"), Sym("yes" if c.get("dnp") else "no")), [Sym("uuid"), sid]]
        if it["vertical"]:
            ref_at = (X + x1 + 1.5, Y - 1.27)
            val_at = (X + x1 + 1.5, Y + 1.27)
        else:
            has_v = any(p["angle"] in (90, 270) for p in it["pins"])
            gap = it["ry"] if has_v else 1.27
            ref_at = (X, Y - (y1 + gap))
            val_at = (X, Y - (y0 - gap))
        props = [("Reference", ref, *ref_at, False),
                 ("Value", str(c.get("value") or ""), *val_at, False),
                 ("Footprint", c["footprint"], X, Y, True), ("Datasheet", "", X, Y, True),
                 ("Description", str(c.get("description") or ""), X, Y, True),
                 ("LCSC", str(c.get("lcsc") or ""), X, Y, True)]
        for key, val, px, py, hide in props:
            eff = [Sym("effects"), [Sym("font"), S(Sym("size"), 1.27, 1.27)]]
            if it["vertical"] and key in ("Reference", "Value"):
                eff.append(S(Sym("justify"), Sym("left")))
            p = [Sym("property"), key, val, S(Sym("at"), px, py, 0)]
            if hide:
                p.append(S(Sym("hide"), Sym("yes")))
            p.append(eff)
            inst.append(p)
        for p in it["pins"]:
            inst.append([Sym("pin"), p["number"], [Sym("uuid"), uid()]])
        inst.append([Sym("instances"), [Sym("project"), name,
                     [Sym("path"), inst_path, S(Sym("reference"), ref), S(Sym("unit"), unit)]]])
        out.append(inst)
        nc = {str(x) for x in c.get("no_connect") or []}
        for p in it["pins"]:
            px, py = X + p["x"], Y - p["y"]
            net = pin_net.get(f"{ref}.{p['number']}")
            if net:
                out.append(_label(label_kind(net), net, px, py, (p["angle"] + 180) % 360))
            elif p["number"] in nc or p["type"] != "no_connect":
                out.append([Sym("no_connect"), S(Sym("at"), px, py), [Sym("uuid"), uid()]])


def _extent(items: list[dict], placed: list, side: list = (), side_pos: list = ()) -> list[float] | None:
    boxes = [(ox, oy, ox + it["w"], oy + it["h"]) for it, (ox, oy) in zip(items, placed)]
    boxes += [(x - 14, y - 4, x + w + 14, y + h + 4) for (w, h), (x, y) in zip(side, side_pos)]
    if not boxes:
        return None
    return [20 + min(b[0] for b in boxes), 20 + min(b[1] for b in boxes),
            20 + max(b[2] for b in boxes), 20 + max(b[3] for b in boxes)]


def build(spec: dict, out_dir: Path) -> dict:
    """Write the schematic: <name>.kicad_sch, plus one sheet per building block when the design uses blocks."""
    spec = design.normalise(spec)
    name = spec["name"]
    pin_net = design.pin_net_map(spec)
    power = {n["name"] for n in spec["nets"] if n.get("class") == "power"}
    lib_symbols: dict[str, list] = {}
    items = []          # one per placed unit
    notes = []
    for c in spec["components"]:
        ref = c["ref"]
        fp = libs.load_footprint(c["footprint"])
        pads = fp.pad_names if fp else []
        node = load_symbol(c["symbol"]) if c.get("symbol") else None
        if node is not None:
            sym_pins = {p["number"] for plist in symbol_pins(node).values() for p in plist}
            missing = [p for p in pads if p not in sym_pins and f"{ref}.{p}" in pin_net]
            if missing:
                notes.append(f"{ref}: symbol {c['symbol']} has no pin numbered "
                             f"{', '.join(missing[:8])}; used a generated symbol instead")
                node = None
        if node is None:
            lib_id = f"WholeBoard:{re.sub(r'[^A-Za-z0-9_]', '_', c['footprint'].split(':')[-1])}"
            if lib_id not in lib_symbols:
                lib_symbols[lib_id] = generic_symbol(lib_id, pads, c["footprint"].split(":")[-1])
            node = lib_symbols[lib_id]
        else:
            lib_symbols.setdefault(str(node[1]), node)
        pins_by_unit = symbol_pins(node)
        units = unit_count(node)
        for unit in range(1, units + 1):
            pins = pins_by_unit.get(0, []) + pins_by_unit.get(unit, [])
            x0, y0, x1, y1 = _bbox(node, unit)
            def room(horizontal: bool) -> float:
                lens = [len(pin_net.get(f"{ref}.{p['number']}", "")) for p in pins
                        if (p["angle"] in (0, 180)) == horizontal]
                return (max(lens) * 1.15 + 2.5) if lens and max(lens) else 2.5
            rx, ry = room(True), room(False)
            vertical = all(p["angle"] in (90, 270) for p in pins) and pins
            prop_w = (max(len(ref), len(str(c.get("value") or ""))) * 1.3 + 3) if vertical else 0
            items.append({"c": c, "node": node, "unit": unit, "pins": pins, "vertical": bool(vertical),
                          "bbox": (x0, y0, x1, y1), "w": (x1 - x0) + 2 * rx + prop_w + 6,
                          "h": (y1 - y0) + 2 * ry + (0 if vertical else 6), "rx": rx, "ry": ry})

    shs = sheets(spec)
    root, subs = shs[0], shs[1:]
    root_uuid = symbol_uuid(name, "#root")
    sheet_of = {ref: sh["name"] for sh in shs for ref in sh["refs"]}
    net_sheets: dict[str, set[str]] = {}
    for pin, net in pin_net.items():
        net_sheets.setdefault(net, set()).add(sheet_of.get(pin.split(".", 1)[0], ""))

    def kind_for(sheet: str):
        def kind(net: str) -> str:
            if net in power:
                return "global_label"
            if len(net_sheets.get(net, ())) <= 1 or not sheet:
                return "label"
            return "hierarchical_label"
        return kind

    pages, notes_out = [], notes
    ports: dict[str, list[str]] = {}
    for n, sh in enumerate(subs, start=2):
        sitems = [it for it in items if it["c"]["ref"] in set(sh["refs"])]
        placed, _, paper, pw, ph = _arrange(sitems, pin_net, power)
        body = _header(sh["uuid"], paper, pw, ph, sh["title"], list(lib_symbols.values()))
        _symbols(body, sitems, placed, name, f"/{root_uuid}/{sh['uuid']}", pin_net, kind_for(sh["name"]))
        body.append(S(Sym("embedded_fonts"), Sym("no")))
        (out_dir / sh["file"]).write_text(sexpr.dumps(body) + "\n", encoding="utf-8")
        ports[sh["name"]] = sorted({net for net, ss in net_sheets.items()
                                    if sh["name"] in ss and len(ss) > 1 and net not in power})
        pages.append({"name": sh["name"], "title": sh["title"], "file": sh["file"], "page": n,
                      "extent": _extent(sitems, placed), "parts": len(sh["refs"])})

    ritems = [it for it in items if it["c"]["ref"] in set(root["refs"])]
    sizes = [(_snap(max(30.0, max([len(p) for p in ports[sh["name"]]] + [len(sh["title"])]) * 1.4 + 10)),
              _snap(max(10.16, (len(ports[sh["name"]]) + 1) * GRID))) for sh in subs]
    placed, side_pos, paper, pw, ph = _arrange(ritems, pin_net, power, sizes)
    sch = _header(root_uuid, paper, pw, ph, spec.get("summary", name) or name, list(lib_symbols.values()))
    _symbols(sch, ritems, placed, name, f"/{root_uuid}", pin_net, kind_for(""))
    for sh, (w, h), (sx, sy), page in zip(subs, sizes, side_pos, pages):
        X, Y = _snap(20 + sx), _snap(20 + sy + 4)
        node = [Sym("sheet"), S(Sym("at"), X, Y), S(Sym("size"), w, h),
                S(Sym("exclude_from_sim"), Sym("no")), S(Sym("in_bom"), Sym("yes")), S(Sym("on_board"), Sym("yes")),
                S(Sym("dnp"), Sym("no")), S(Sym("fields_autoplaced"), Sym("yes")),
                [Sym("stroke"), S(Sym("width"), 0.1524), S(Sym("type"), Sym("solid"))],
                [Sym("fill"), S(Sym("color"), 0, 0, 0, 0.0)], [Sym("uuid"), sh["uuid"]],
                [Sym("property"), "Sheetname", sh["name"], S(Sym("at"), X, Y - 0.7116, 0),
                 [Sym("effects"), [Sym("font"), S(Sym("size"), 1.27, 1.27)], S(Sym("justify"), Sym("left"), Sym("bottom"))]],
                [Sym("property"), "Sheetfile", sh["file"], S(Sym("at"), X, Y + h + 0.5846, 0),
                 [Sym("effects"), [Sym("font"), S(Sym("size"), 1.27, 1.27)], S(Sym("justify"), Sym("left"), Sym("top"))]]]
        for k, net in enumerate(ports[sh["name"]]):
            py = Y + GRID * (k + 1)
            node.append([Sym("pin"), net, Sym("passive"), S(Sym("at"), X, py, 180), [Sym("uuid"), uid()],
                         [Sym("effects"), [Sym("font"), S(Sym("size"), 1.27, 1.27)], S(Sym("justify"), Sym("left"))]])
            sch.append(_label("label", net, X, py, 180))
        node.append([Sym("instances"), [Sym("project"), name, [Sym("path"), f"/{root_uuid}", S(Sym("page"), str(page["page"]))]]])
        sch.append(node)
    sch.append([Sym("sheet_instances"), [Sym("path"), "/", S(Sym("page"), "1")]])
    sch.append(S(Sym("embedded_fonts"), Sym("no")))
    path = out_dir / f"{name}.kicad_sch"
    path.write_text(sexpr.dumps(sch) + "\n", encoding="utf-8")
    extent = _extent(ritems, placed, sizes, side_pos)
    pages.insert(0, {"name": "", "title": "Main", "file": path.name, "page": 1, "extent": extent,
                     "parts": len(root["refs"])})
    return {"sch": str(path), "paper": paper, "notes": notes_out, "extent": extent, "pages": pages}


def crop_svg(svg: Path, extent: list[float], margin: float = 6.0) -> None:
    """Crop an exported schematic SVG (units: mm) to the drawn area."""
    x0, y0, x1, y1 = extent[0] - margin, extent[1] - margin, extent[2] + margin, extent[3] + margin
    text = svg.read_text(encoding="utf-8")
    text = re.sub(r'width="[\d.]+mm" height="[\d.]+mm" viewBox="[\d. ]+"',
                  f'width="{x1 - x0:.4f}mm" height="{y1 - y0:.4f}mm" '
                  f'viewBox="{x0:.4f} {y0:.4f} {x1 - x0:.4f} {y1 - y0:.4f}"', text, count=1)
    svg.write_text(text, encoding="utf-8")


def _pack(items: list[dict], width: float, height: float) -> list[tuple[float, float]] | None:
    order = sorted(range(len(items)), key=lambda i: -items[i]["h"])
    pos: dict[int, tuple[float, float]] = {}
    x = y = shelf_h = 0.0
    for i in order:
        w, h = items[i]["w"], items[i]["h"]
        if w > width:
            return None
        if x + w > width:
            x, y, shelf_h = 0.0, y + shelf_h, 0.0
        if y + h > height:
            return None
        pos[i] = (x, y)
        x += w
        shelf_h = max(shelf_h, h)
    return [pos[i] for i in range(len(items))]
