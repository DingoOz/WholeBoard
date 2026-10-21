"""Write a KiCad 10 schematic (.kicad_sch) from a design spec.

Every component gets its library symbol (or a generated box symbol when the
spec names none), laid out on a grid. Pins connect through net labels, so the
schematic is electrically identical to the board's netlist.
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


def build(spec: dict, out_dir: Path) -> dict:
    spec = design.normalise(spec)
    name = spec["name"]
    pin_net = design.pin_net_map(spec)
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

    # shelf packing on the smallest paper that fits
    for paper, pw, ph in PAPERS:
        placed = _pack(items, pw - 40, ph - 40)
        if placed is not None:
            break
    else:
        paper, pw, ph = "User", 1600.0, 1200.0
        placed = _pack(items, pw - 40, ph - 40) or []

    root_uuid = symbol_uuid(name, "#root")
    sch: list = [Sym("kicad_sch"), S(Sym("version"), Sym(FORMAT_VERSION)), S(Sym("generator"), "eeschema"),
                 S(Sym("generator_version"), "10.0"), [Sym("uuid"), root_uuid],
                 S(Sym("paper"), paper) if paper != "User" else S(Sym("paper"), "User", pw, ph),
                 [Sym("title_block"), [Sym("title"), spec.get("summary", name)[:80] or name],
                  [Sym("company"), "Generated by WholeBoard"]],
                 [Sym("lib_symbols"), *lib_symbols.values()]]

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
                     [Sym("path"), f"/{root_uuid}", S(Sym("reference"), ref), S(Sym("unit"), unit)]]])
        sch.append(inst)
        nc = {str(x) for x in c.get("no_connect") or []}
        for p in it["pins"]:
            px, py = X + p["x"], Y - p["y"]
            net = pin_net.get(f"{ref}.{p['number']}")
            if net:
                ang = (p["angle"] + 180) % 360
                just = "left" if ang in (0, 90) else "right"
                sch.append([Sym("global_label"), net, S(Sym("shape"), Sym("passive")),
                            S(Sym("at"), px, py, ang), S(Sym("fields_autoplaced"), Sym("yes")),
                            [Sym("effects"), [Sym("font"), S(Sym("size"), 1.27, 1.27)],
                             S(Sym("justify"), Sym(just))], [Sym("uuid"), uid()]])
            elif p["number"] in nc or p["type"] != "no_connect":
                sch.append([Sym("no_connect"), S(Sym("at"), px, py), [Sym("uuid"), uid()]])

    sch.append([Sym("sheet_instances"), [Sym("path"), "/", S(Sym("page"), "1")]])
    sch.append(S(Sym("embedded_fonts"), Sym("no")))
    path = out_dir / f"{name}.kicad_sch"
    path.write_text(sexpr.dumps(sch) + "\n", encoding="utf-8")
    extent = None
    if placed:
        xs0 = [20 + ox for ox, _ in placed]
        ys0 = [20 + oy for _, oy in placed]
        xs1 = [20 + ox + it["w"] for it, (ox, _) in zip(items, placed)]
        ys1 = [20 + oy + it["h"] for it, (_, oy) in zip(items, placed)]
        extent = [min(xs0), min(ys0), max(xs1), max(ys1)]
    return {"sch": str(path), "paper": paper, "notes": notes, "extent": extent}


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
