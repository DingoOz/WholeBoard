"""Where every net and part appears on the schematic's sheets, for tracing a net in the browser.

The data sits beside the schematic's SVGs. KiCad exports a schematic SVG in millimetres with the sheet's own
coordinates as the SVG's user units (and `schematic.crop_svg` only changes the viewBox), so every position here is
in schematic millimetres, which is also the SVG's coordinate system.

    {"version": 1,
     "sheets": [{"name": "", "title": "Main", "file": "x.kicad_sch", "viewBox": [x, y, w, h], "url": ...}],
     "nets":   {"GND": {"power": true, "items": [{"s": 0, "k": "global_label", "x": .., "y": .., "box": [..]},
                                                 {"s": 0, "k": "pin", "ref": "U1", "pin": "3", "name": "GND",
                                                  "x": .., "y": .., "x2": .., "y2": ..},
                                                 {"s": 1, "k": "wire", "x": .., "y": .., "x2": .., "y2": ..}]}},
     "parts":  {"U1": {"value": "..", "items": [{"s": 0, "unit": 1, "box": [x0, y0, x1, y1]}]}},
     "blocks": [{"s": 0, "sheet": "MCU", "box": [x0, y0, x1, y1]}]}

Item kinds: label, global_label, hierarchical_label, power (a power symbol), pin, sheet_pin, wire, bus,
bus_label. A box is [x0, y0, x1, y1] with y down.

`write_generated` takes what `schematic.build` recorded; `read` reads a KiCad project's own .kicad_sch files
(for an imported design), naming each connected group from the design's netlist.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .. import sexpr
from ..sexpr import find, find_all, num, tag

FONT = 1.27          # label text height, mm
CHAR_W = 0.95        # a label character's advance, mm (KiCad's stroke font at 1.27 mm)


def label_box(x: float, y: float, angle: float, text: str, kind: str, size: float = FONT) -> list[float]:
    """The rectangle a label's text covers: it runs away from its anchor along its angle (0 right, 90 up,
    180 left, 270 down). Global and hierarchical labels add their flag."""
    length = len(text) * CHAR_W * size / FONT + (2.0 if kind in ("global_label", "hierarchical_label") else 0.4)
    half = size * 0.75
    a = round(angle) % 360
    if a == 0:
        b = [x, y - half, x + length, y + half]
    elif a == 180:
        b = [x - length, y - half, x, y + half]
    elif a == 90:
        b = [x - half, y - length, x + half, y]
    else:
        b = [x - half, y, x + half, y + length]
    return [round(v, 3) for v in b]


def view_box(svg: Path) -> list[float] | None:
    """The viewBox of an exported (and perhaps cropped) schematic SVG."""
    try:
        head = svg.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return None
    m = re.search(r'viewBox="([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)"', head)
    return [float(v) for v in m.groups()] if m else None


class Collector:
    """Gathers the items of every sheet, then writes the sidecar."""

    def __init__(self) -> None:
        self.sheets: list[dict] = []
        self.nets: dict[str, dict] = {}
        self.parts: dict[str, dict] = {}
        self.blocks: list[dict] = []

    def sheet(self, name: str, title: str, file: str) -> int:
        self.sheets.append({"name": name, "title": title, "file": file})
        return len(self.sheets) - 1

    def net(self, name: str, power: bool = False) -> dict:
        n = self.nets.setdefault(name, {"power": False, "items": []})
        n["power"] = n["power"] or power
        return n

    def add(self, net: str, s: int, kind: str, x: float, y: float, power: bool = False, **extra) -> None:
        item = {"s": s, "k": kind, "x": round(x, 3), "y": round(y, 3)}
        for k, v in extra.items():
            item[k] = round(v, 3) if isinstance(v, float) else v
        self.net(net, power)["items"].append(item)

    def label(self, net: str, s: int, kind: str, x: float, y: float, angle: float, text: str | None = None,
              power: bool = False) -> None:
        self.add(net, s, kind, x, y, power, angle=angle, text=text if text is not None else net,
                 box=label_box(x, y, angle, text if text is not None else net, kind))

    def part(self, ref: str, s: int, unit: int, box, value: str = "") -> None:
        p = self.parts.setdefault(ref, {"value": value, "items": []})
        p["items"].append({"s": s, "unit": unit, "box": [round(v, 3) for v in box]})

    def data(self) -> dict:
        for n in self.nets.values():
            n["items"].sort(key=lambda i: (i["s"], i["k"] == "wire", i["y"], i["x"]))
        return {"version": 1, "sheets": self.sheets, "nets": dict(sorted(self.nets.items())),
                "parts": dict(sorted(self.parts.items(), key=lambda kv: _ref_key(kv[0]))), "blocks": self.blocks}


def _ref_key(ref: str):
    m = re.match(r"([A-Za-z_#]*)(\d*)", ref)
    return (m.group(1), int(m.group(2) or 0), ref)


def finish(data: dict, svgs: dict[str, Path], urls: dict[str, str] | None = None) -> dict:
    """Put each sheet's SVG viewBox (and URL) in the data: svgs maps a sheet name to its exported SVG."""
    for sh in data["sheets"]:
        f = svgs.get(sh["name"])
        if f is not None:
            sh["viewBox"] = view_box(f)
            if urls and sh["name"] in urls:
                sh["url"] = urls[sh["name"]]
    return data


def save(data: dict, path: Path) -> Path:
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return path


# ------------------------------------------------------------------------------------------------------------
# Reading a KiCad project's own schematic

def _xy(node) -> tuple[float, float]:
    return num(node[1]), num(node[2])


def _transform(px: float, py: float, at: tuple[float, float, float], mirror: str | None) -> tuple[float, float]:
    """A library point (y up) to sheet coordinates (y down) for a symbol at (x, y, angle) with an optional
    mirror: KiCad turns the symbol anticlockwise on the sheet, then mirrors it (checked against kicad-cli's
    netlist for every angle and mirror)."""
    x, y = px, -py
    a = math.radians(at[2])
    c, s = round(math.cos(a), 9), round(math.sin(a), 9)
    x, y = x * c + y * s, -x * s + y * c
    if mirror == "x":       # mirrored about the X axis: upside down
        y = -y
    elif mirror == "y":     # mirrored about the Y axis: left to right
        x = -x
    return at[0] + x, at[1] + y


def _lib_pins(node: list) -> dict[int, list[dict]]:
    """A library symbol's pins per unit (unit 0: shared by every unit), each with its two ends."""
    units: dict[int, list[dict]] = {}
    for sub in find_all(node, "symbol"):
        m = re.search(r"_(\d+)_(\d+)$", str(sub[1]))
        if not m or int(m.group(2)) > 1:
            continue
        for p in find_all(sub, "pin"):
            at = find(p, "at")
            ln = find(p, "length")
            nm, no = find(p, "name"), find(p, "number")
            x, y = num(at[1]), num(at[2])
            ang = math.radians(num(at[3]) if len(at) > 3 else 0)
            length = num(ln[1]) if ln else 2.54
            units.setdefault(int(m.group(1)), []).append({
                "x": x, "y": y, "x2": x + length * math.cos(ang), "y2": y + length * math.sin(ang),
                "name": str(nm[1]) if nm else "", "number": str(no[1]) if no else ""})
    return units


def _lib_box(node: list, unit: int) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for sub in find_all(node, "symbol"):
        m = re.search(r"_(\d+)_(\d+)$", str(sub[1]))
        if not m or int(m.group(1)) not in (0, unit) or int(m.group(2)) > 1:
            continue
        for pt in sexpr.walk(sub):
            if tag(pt) in ("start", "end", "xy", "mid", "center", "at"):
                xs.append(num(pt[1])); ys.append(num(pt[2]))
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (-1.27, -1.27, 1.27, 1.27)


def _prop(node: list, name: str) -> str:
    for p in find_all(node, "property"):
        if len(p) > 2 and str(p[1]) == name:
            return str(p[2])
    return ""


def _instance_ref(sym: list, path: str) -> str:
    """The reference a symbol has on this instance of its sheet (a sheet used twice numbers its parts twice)."""
    inst = find(sym, "instances")
    best = ""
    for proj in find_all(inst, "project") if inst else []:
        for pth in find_all(proj, "path"):
            r = find(pth, "reference")
            if r and str(pth[1]) == path:
                return str(r[1])
            if r and not best:
                best = str(r[1])
    return best or _prop(sym, "Reference")


def _page_number(sheet: list) -> float:
    """A sheet symbol's page number (its first instance's), so the sheets come in the order KiCad numbers them."""
    for pth in sexpr.walk(sheet):
        if tag(pth) == "path":
            pg = find(pth, "page")
            if pg and re.fullmatch(r"\d+(\.\d+)?", str(pg[1])):
                return float(pg[1])
    return 1e9


class _Union:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, a: int) -> int:
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def join(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


def _on_segment(p, a, b, tol: float = 0.01) -> bool:
    (px, py), (ax, ay), (bx, by) = p, a, b
    if min(ax, bx) - tol > px or px > max(ax, bx) + tol or min(ay, by) - tol > py or py > max(ay, by) + tol:
        return False
    return abs((bx - ax) * (py - ay) - (by - ay) * (px - ax)) <= tol * max(1.0, math.dist(a, b))


def bus_members(text: str) -> list[str]:
    """The nets a bus label names: D[0..7] -> D0..D7, {SDA SCL} -> SDA, SCL (with a group prefix, I2C.SDA)."""
    text = text.strip()
    m = re.fullmatch(r"(.*?)\[(\d+)\.\.(\d+)\]", text)
    if m:
        a, b = int(m.group(2)), int(m.group(3))
        step = 1 if b >= a else -1
        return [f"{m.group(1)}{i}" for i in range(a, b + step, step)]
    m = re.fullmatch(r"([^{]*)\{([^}]*)\}", text)
    if m:
        out = []
        for part in m.group(2).split():
            out += [f"{m.group(1)}.{x}" if m.group(1) else x for x in (bus_members(part) or [part])]
        return out
    return []


def _clean(name: str) -> str:
    """A KiCad net or label name in the form the importer gives nets (no sheet path, safe characters)."""
    name = name.strip().split("/")[-1] if name.strip().startswith("/") else name.strip()
    name = re.sub(r"^Net-\((.+)\)$", r"N_\1", name)
    return re.sub(r"[^A-Za-z0-9_+.\-]+", "_", name).strip("_") or name


def read(root: Path, pin_net: dict[str, str] | None = None) -> dict:
    """The sidecar data for a KiCad project's own schematic (root sheet and every sub-sheet instance).

    pin_net maps "REF.PIN" to the design's net name, as the importer named the nets; a connected group of wires,
    pins and labels takes the net of a pin in it, or else the name of its label."""
    pin_net = pin_net or {}
    col = Collector()
    trees: dict[Path, list] = {}

    def tree(f: Path) -> list | None:
        if f not in trees:
            try:
                trees[f] = sexpr.parse(f.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                trees[f] = None
        return trees[f]

    todo = [(root, "", "Main", "/" + str((find(tree(root) or [], "uuid") or ["", ""])[1]))]
    seen = 0
    while todo and seen < 200:
        f, name, title, path = todo.pop(0)
        seen += 1
        t = tree(f)
        if t is None:
            continue
        s = col.sheet(name, title, f.name)
        for sh in sorted(find_all(t, "sheet"), key=_page_number):
            sname, sfile = _prop(sh, "Sheetname") or _prop(sh, "Sheet name"), _prop(sh, "Sheetfile") or _prop(sh, "Sheet file")
            su = find(sh, "uuid")
            at, size = find(sh, "at"), find(sh, "size")
            if sfile and (f.parent / sfile).exists():
                todo.append((f.parent / sfile, sname or Path(sfile).stem, sname or Path(sfile).stem,
                             f"{path}/{su[1] if su else ''}"))
            if at and size:
                x, y = _xy(at)
                col.blocks.append({"s": s, "sheet": sname or Path(sfile).stem,
                                   "box": [round(x, 3), round(y, 3), round(x + num(size[1]), 3), round(y + num(size[2]), 3)]})
        _read_sheet(t, s, path, col, pin_net)
    return col.data()


def _read_sheet(t: list, s: int, path: str, col: Collector, pin_net: dict[str, str]) -> None:
    libs = {str(n[1]): n for n in (find(t, "lib_symbols") or [])[1:] if tag(n) == "symbol"}
    points: list[tuple[float, float]] = []
    items: list[dict] = []            # every connectable item, with the index of its point(s)

    def pt(x: float, y: float) -> int:
        points.append((round(x, 3), round(y, 3)))
        return len(points) - 1

    wires = []
    for kind in ("wire", "bus"):
        for w in find_all(t, kind):
            pts = find(w, "pts")
            xy = [_xy(p) for p in pts[1:] if tag(p) == "xy"] if pts else []
            for a, b in zip(xy, xy[1:]):
                it = {"k": kind, "x": a[0], "y": a[1], "x2": b[0], "y2": b[1], "p": [pt(*a), pt(*b)]}
                items.append(it)
                if kind == "wire":
                    wires.append(it)
    for e in find_all(t, "bus_entry"):
        at, size = find(e, "at"), find(e, "size")
        if at and size:
            x, y = _xy(at)
            items.append({"k": "bus_entry", "x": x, "y": y, "x2": x + num(size[1]), "y2": y + num(size[2]),
                          "p": [pt(x, y), pt(x + num(size[1]), y + num(size[2]))]})
    for kind in ("label", "global_label", "hierarchical_label"):
        for lb in find_all(t, kind):
            at = find(lb, "at")
            x, y = _xy(at)
            items.append({"k": kind, "text": str(lb[1]), "x": x, "y": y, "angle": num(at[3]) if len(at) > 3 else 0,
                          "p": [pt(x, y)]})
    for sh in find_all(t, "sheet"):
        for p in find_all(sh, "pin"):
            at = find(p, "at")
            x, y = _xy(at)
            items.append({"k": "sheet_pin", "text": str(p[1]), "x": x, "y": y, "p": [pt(x, y)]})
    for sym in find_all(t, "symbol"):
        lid = find(sym, "lib_id")
        if not lid:
            continue
        node = libs.get(str(lid[1]))
        at_n = find(sym, "at")
        if node is None or not at_n:
            continue
        at = (num(at_n[1]), num(at_n[2]), num(at_n[3]) if len(at_n) > 3 else 0.0)
        mir = find(sym, "mirror")
        mirror = str(mir[1]) if mir else None
        un = find(sym, "unit")
        unit = int(num(un[1])) if un else 1
        ref = _instance_ref(sym, path)
        power = find(node, "power") is not None or ref.startswith("#PWR")
        pins = _lib_pins(node)
        if power:
            value = _prop(sym, "Value") or str(lid[1]).split(":")[-1]
            for p in pins.get(0, []) + pins.get(unit, []):
                x, y = _transform(p["x"], p["y"], at, mirror)
                items.append({"k": "power", "text": value, "x": x, "y": y, "p": [pt(x, y)],
                              "box": _sym_box(node, unit, at, mirror)})
            continue
        if ref.startswith("#"):
            continue
        col.part(ref, s, unit, _sym_box(node, unit, at, mirror), _prop(sym, "Value"))
        for p in pins.get(0, []) + pins.get(unit, []):
            x, y = _transform(p["x"], p["y"], at, mirror)
            x2, y2 = _transform(p["x2"], p["y2"], at, mirror)
            items.append({"k": "pin", "ref": ref, "pin": p["number"], "name": p["name"], "x": x, "y": y,
                          "x2": x2, "y2": y2, "p": [pt(x, y)]})

    # connectivity: equal points join, and a point on a wire joins that wire
    u = _Union()
    exact: dict[tuple[float, float], int] = {}
    for i, q in enumerate(points):
        if q in exact:
            u.join(i, exact[q])
        else:
            exact[q] = i
    for it in items:
        if len(it["p"]) == 2 and it["k"] != "bus_entry":     # a bus entry's ends are on the bus and on a wire
            u.join(it["p"][0], it["p"][1])
    for w in wires + [i for i in items if i["k"] == "bus"]:
        a, b = (w["x"], w["y"]), (w["x2"], w["y2"])
        for q, i in exact.items():
            if _on_segment(q, a, b):
                u.join(i, w["p"][0])

    groups: dict[int, list[dict]] = {}
    for it in items:
        groups.setdefault(u.find(it["p"][0]), []).append(it)
    for members in groups.values():
        net = next((pin_net[f"{i['ref']}.{i['pin']}"] for i in members
                    if i["k"] == "pin" and f"{i['ref']}.{i['pin']}" in pin_net), None)
        if net is None:
            labels = [i for i in members if i["k"] in ("power", "global_label", "hierarchical_label", "label",
                                                       "sheet_pin") and not bus_members(i["text"])]
            net = _clean(labels[0]["text"]) if labels else None
        is_bus = any(i["k"] == "bus" for i in members)
        targets = [net] if net and not is_bus else []
        if is_bus:
            for i in members:
                if "text" in i:
                    targets += [_clean(m) for m in bus_members(i["text"])]
            targets = list(dict.fromkeys(targets))
        for name in targets:
            power = any(i["k"] == "power" for i in members)
            for i in members:
                extra = {k: i[k] for k in ("ref", "pin", "name", "x2", "y2", "text", "box") if k in i}
                kind = "wire" if i["k"] == "bus_entry" else i["k"]
                if is_bus and kind == "label":
                    kind = "bus_label"
                if kind in ("label", "global_label", "hierarchical_label", "bus_label"):
                    col.label(name, s, kind, i["x"], i["y"], i.get("angle", 0), i["text"], power)
                else:
                    col.add(name, s, kind, i["x"], i["y"], power, **extra)


def _sym_box(node: list, unit: int, at, mirror) -> list[float]:
    x0, y0, x1, y1 = _lib_box(node, unit)
    corners = [_transform(x, y, at, mirror) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    return [min(xs), min(ys), max(xs), max(ys)]
