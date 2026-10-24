"""The design spec the AI writes, and its validation against the KiCad libraries.

A spec is plain JSON:

{
  "name": "usb_led_blinker",
  "summary": "one paragraph",
  "board": {"width_mm": 50, "height_mm": 30, "layers": 2, "thickness_mm": 1.6,
            "corner_radius_mm": 1.0, "mounting_holes": {"diameter_mm": 3.2, "inset_mm": 3.5},
            "ground_pour": "GND", "planes": {"In1.Cu": "GND", "In2.Cu": "+3V3"},
            "test_points": true},
  "rules": {"track_mm": 0.25, "power_track_mm": 0.5, "clearance_mm": 0.2,
            "via_diameter_mm": 0.6, "via_drill_mm": 0.3},
  "components": [{"ref": "U1", "value": "AMS1117-3.3",
                  "footprint": "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
                  "symbol": "Regulator_Linear:AMS1117-3.3", "lcsc": "C6186",
                  "description": "3.3 V LDO", "no_connect": ["4"],
                  "placement": {"x_mm": 5, "y_mm": 15, "rotation": 90, "side": "top", "locked": true}}],
  "nets": [{"name": "GND", "class": "power", "pins": ["U1.1", "C1.2"]}]
}

Pin references are "REF.PAD" where PAD is the footprint pad name.
A component may also carry "height_mm" (its datasheet height above the board) for the enclosure.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from . import blocks, impedance, mains
from .kicad import libs

REF_RE = re.compile(r"^[A-Z]+[A-Z_]*\d+$")

DEFAULT_RULES = {
    # Comfortable JLCPCB standard-process values (their minimums are smaller).
    "track_mm": 0.25,
    "power_track_mm": 0.5,
    "clearance_mm": 0.2,
    "via_diameter_mm": 0.6,
    "via_drill_mm": 0.3,
    "edge_clearance_mm": 0.3,
}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}


def normalise(spec: dict) -> dict:
    """Fill defaults so later stages can rely on every key."""
    spec = blocks.expand(dict(spec))
    spec.setdefault("name", "board")
    spec["name"] = re.sub(r"[^A-Za-z0-9_-]+", "_", str(spec["name"])).strip("_") or "board"
    b = dict(spec.get("board") or {})
    if b.get("outline"):
        pts = [[float(p[0]), float(p[1])] for p in b["outline"]]
        mx, my = min(p[0] for p in pts), min(p[1] for p in pts)
        b["outline"] = [[round(p[0] - mx, 4), round(p[1] - my, 4)] for p in pts]
        b["width_mm"] = round(max(p[0] for p in b["outline"]), 4)
        b["height_mm"] = round(max(p[1] for p in b["outline"]), 4)
    b.setdefault("width_mm", 50.0)
    b.setdefault("height_mm", 50.0)
    b.setdefault("layers", 2)
    b.setdefault("thickness_mm", 1.6)
    b.setdefault("corner_radius_mm", 1.0)
    b.setdefault("mounting_holes", None)
    b.setdefault("ground_pour", "GND")
    b.setdefault("planes", {})
    spec["board"] = b
    spec["rules"] = {**DEFAULT_RULES, **(spec.get("rules") or {})}
    spec["components"] = [dict(c) for c in spec.get("components") or []]
    spec["nets"] = [dict(n) for n in spec.get("nets") or []]
    for n in spec["nets"]:
        n.setdefault("class", "signal")
        n["pins"] = [str(p) for p in n.get("pins") or []]
    if b.get("fiducials") and not any(c.get("fiducial") for c in spec["components"]):
        _add_fiducials(spec)
    if b.get("test_points"):
        _add_test_points(spec, b["test_points"])
        _place_test_points(spec)
    return spec


# Footprints that are only copper or holes: nothing is bought or placed for them (solder jumpers are
# bridged, or cut, by hand).
COPPER_ONLY = ("MountingHole", "TestPoint", "Fiducial", "NetTie", "Jumper:SolderJumper")


def copper_only(footprint: str | None) -> bool:
    return str(footprint or "").startswith(COPPER_ONLY)


TEST_POINT_FP = "TestPoint:TestPoint_Pad_D1.5mm"
FIDUCIAL_FP = "Fiducial:Fiducial_1mm_Mask2mm"
FINE_PITCH = re.compile(r"_P0\.([0-4]\d*|50*)mm")


def _fixed_boxes(spec: dict) -> list[tuple]:
    boxes = []
    for c in spec["components"]:
        pl = c.get("placement") or {}
        if "x_mm" not in pl:
            continue
        fp = libs.load_footprint(c.get("footprint") or "")
        x0, y0, x1, y1 = fp.courtyard if fp else (-2, -2, 2, 2)
        if round(float(pl.get("rotation") or 0)) % 180 == 90:
            x0, y0, x1, y1 = y0, x0, y1, x1
        x, y = float(pl["x_mm"]), float(pl["y_mm"])
        boxes.append((x + x0, y + y0, x + x1, y + y1))
    return boxes


def _add_fiducials(spec: dict) -> None:
    """Three fiducials for the assembly machine's camera, in three corners (not the fourth, so the board's
    orientation is unambiguous), clear of mounting holes and fixed parts."""
    b = spec["board"]
    w, h = float(b["width_mm"]), float(b["height_mm"])
    poly = b.get("outline") or [[0, 0], [w, 0], [w, h], [0, h]]
    holes = hole_positions(spec)
    hfp = libs.load_footprint(hole_footprint(spec)) if holes else None
    hole_r = max(abs(v) for v in hfp.courtyard) if hfp else 3.5
    r = 1.5                                       # the 2 mm mask opening's courtyard plus a margin
    boxes = _fixed_boxes(spec)
    used = {str(c.get("ref")) for c in spec["components"]}
    k = 1
    for cx, cy, sx, sy in ((0, 0, 1, 1), (w, 0, -1, 1), (0, h, 1, -1)):
        spots = sorted(((cx + sx * dx, cy + sy * dy) for dx in _steps(2.5, min(15, w / 2)) for dy in _steps(2.5, min(15, h / 2))),
                       key=lambda p: math.hypot(p[0] - cx, p[1] - cy))
        for x, y in spots:
            if not inside(poly, x, y) or edge_distance(poly, x, y) < 2.5:
                continue
            if any(math.hypot(x - hx, y - hy) < hole_r + r for hx, hy in holes):
                continue
            if any(bx0 - r < x < bx1 + r and by0 - r < y < by1 + r for bx0, by0, bx1, by1 in boxes):
                continue
            while f"FID{k}" in used:
                k += 1
            used.add(f"FID{k}")
            spec["components"].append({"ref": f"FID{k}", "value": "Fiducial", "footprint": FIDUCIAL_FP,
                                       "symbol": "Mechanical:Fiducial",
                                       "description": "fiducial for the assembly machine", "fiducial": True,
                                       "sim": {"skip": True},
                                       "placement": {"x_mm": round(x, 2), "y_mm": round(y, 2), "locked": True}})
            boxes.append((x - r, y - r, x + r, y + r))
            break


def _add_test_points(spec: dict, which) -> None:
    """Bare copper pads on the supply rails (or the nets listed), for probing during bring-up."""
    have = {c.get("test_point_for") for c in spec["components"] if c.get("test_point_for")}
    nets = {n["name"]: n for n in spec["nets"]}
    if isinstance(which, list):
        wanted = [str(x) for x in which]
    else:
        wanted = [n["name"] for n in spec["nets"] if n.get("class") == "power"][:8]
    used = {str(c.get("ref")) for c in spec["components"]}
    k = 1
    for net in wanted:
        if net in have or net not in nets:
            continue
        while f"TP{k}" in used:
            k += 1
        ref = f"TP{k}"
        used.add(ref)
        spec["components"].append({"ref": ref, "value": net, "footprint": TEST_POINT_FP, "symbol": "Connector:TestPoint",
                                   "description": f"Test point for {net}", "test_point_for": net,
                                   "sim": {"skip": True}})
        nets[net]["pins"].append(f"{ref}.1")


def _place_test_points(spec: dict) -> None:
    """Put unplaced test pads in a row along a board edge, where a probe can reach them.

    TraceMaker's placer does not move test points (and its routability check stalls on them), so they are fixed
    here: along the bottom edge first, then the top, left and right, clear of mounting holes and fixed parts."""
    tps = [c for c in spec["components"] if c.get("test_point_for") and "x_mm" not in (c.get("placement") or {})]
    if not tps:
        return
    b = spec["board"]
    w, h = float(b["width_mm"]), float(b["height_mm"])
    poly = b.get("outline") or [[0, 0], [w, 0], [w, h], [0, h]]
    holes = hole_positions(spec)
    hfp = libs.load_footprint(hole_footprint(spec)) if holes else None
    hole_r = max(abs(v) for v in hfp.courtyard) if hfp else 3.5
    tfp = libs.load_footprint(TEST_POINT_FP)
    r = (max(abs(v) for v in tfp.courtyard) if tfp else 1.25) + 0.25   # test pad courtyard plus a margin
    boxes = []                                    # fixed parts' courtyards on the board
    for c in spec["components"]:
        pl = c.get("placement") or {}
        if "x_mm" not in pl or c.get("test_point_for"):
            continue
        fp = libs.load_footprint(c.get("footprint") or "")
        x0, y0, x1, y1 = fp.courtyard if fp else (-2, -2, 2, 2)
        if round(float(pl.get("rotation") or 0)) % 180 == 90:
            x0, y0, x1, y1 = y0, x0, y1, x1
        x, y = float(pl["x_mm"]), float(pl["y_mm"])
        boxes.append((x + x0, y + y0, x + x1, y + y1))
    taken = [(float(c["placement"]["x_mm"]), float(c["placement"]["y_mm"])) for c in spec["components"]
             if c.get("test_point_for") and "x_mm" in (c.get("placement") or {})]
    inset = 2.0                                   # pad centre from the board edge
    lines = [[(x, h - inset) for x in _steps(inset, w - inset)], [(x, inset) for x in _steps(inset, w - inset)],
             [(inset, y) for y in _steps(inset, h - inset)], [(w - inset, y) for y in _steps(inset, h - inset)]]
    for x, y in (pt for line in lines for pt in line):
        if not tps:
            return
        if not inside(poly, x, y) or edge_distance(poly, x, y) < inset - 0.01:
            continue
        if any(math.hypot(x - hx, y - hy) < hole_r + r for hx, hy in holes):
            continue
        if any(bx0 - r < x < bx1 + r and by0 - r < y < by1 + r for bx0, by0, bx1, by1 in boxes):
            continue
        if any(math.hypot(x - tx, y - ty) < 2 * r for tx, ty in taken):
            continue
        tps.pop(0)["placement"] = {"x_mm": round(x, 2), "y_mm": round(y, 2), "locked": True}
        taken.append((x, y))


def _steps(a: float, b: float, step: float = 0.5) -> list[float]:
    return [round(a + i * step, 3) for i in range(int((b - a) / step) + 1)]


def validate(spec: dict) -> Report:
    spec = normalise(spec)
    rep = Report()
    b = spec["board"]
    try:
        w, h = float(b["width_mm"]), float(b["height_mm"])
    except (TypeError, ValueError):
        rep.errors.append("board.width_mm and board.height_mm must be numbers")
        return rep
    if not (5 <= w <= 500 and 5 <= h <= 500):
        rep.errors.append("board size must be between 5 and 500 mm on each side")
    if b["layers"] not in (1, 2, 4, 6):
        rep.errors.append("board.layers must be 1, 2, 4 or 6")
    for layer, net in (b.get("planes") or {}).items():
        if b["layers"] < 4:
            rep.errors.append("board.planes needs a 4 or 6 layer board")
            break
        if layer not in ("In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu"):
            rep.errors.append(f"board.planes: unknown layer {layer!r}")

    rep.errors += spec.get("block_errors") or []
    case = b.get("case")
    if case is not None:
        inner = case.get("inner_mm") if isinstance(case, dict) else None
        if not (isinstance(inner, list) and len(inner) == 3 and all(isinstance(v, (int, float)) and v > 0 for v in inner)):
            rep.errors.append("board.case.inner_mm must be [length, width, height] in mm, from the box maker's drawing")
        elif w > inner[0] or h > inner[1]:
            rep.errors.append(f"the {w:g} × {h:g} mm board is bigger than the case's {inner[0]:g} × {inner[1]:g} mm inside")
    if not spec["components"]:
        rep.errors.append("the design has no components")

    for c in spec["components"]:
        hv = c.get("height_mm")
        if hv is not None and (not isinstance(hv, (int, float)) or not 0 <= hv <= 100):
            rep.errors.append(f"{c.get('ref')}: height_mm must be a number of millimetres")

    fps: dict[str, libs.Footprint] = {}
    refs: set[str] = set()
    area = 0.0
    for c in spec["components"]:
        ref = str(c.get("ref", ""))
        if not REF_RE.match(ref):
            rep.errors.append(f"component reference {ref!r} must look like R1, U2, J3")
            continue
        if ref in refs:
            rep.errors.append(f"duplicate reference {ref}")
        refs.add(ref)
        fid = str(c.get("footprint", ""))
        fp = libs.load_footprint(fid)
        if not fp:
            name = fid.split(":")[-1]
            hint = libs.search_footprints(re.sub(r"[_-]", " ", name)[:40], 6)
            rep.errors.append(f"{ref}: footprint {fid!r} not found in the KiCad libraries"
                              + (f"; close matches: {', '.join(hint)}" if hint else ""))
            continue
        fps[ref] = fp
        x0, y0, x1, y1 = fp.courtyard
        area += (x1 - x0) * (y1 - y0)
        if not c.get("lcsc") and not c.get("dnp") and not copper_only(fid):
            rep.warnings.append(f"{ref}: no LCSC part number, so JLCPCB cannot assemble or price it")

    used: dict[str, str] = {}
    names: set[str] = set()
    for n in spec["nets"]:
        name = str(n.get("name", "")).strip()
        if not name:
            rep.errors.append("a net has no name")
            continue
        if name in names:
            rep.errors.append(f"net {name} is listed twice; merge its pins into one entry")
        names.add(name)
        if len(n["pins"]) < 2:
            rep.warnings.append(f"net {name} has fewer than two pins")
        for pin in n["pins"]:
            if "." not in pin:
                rep.errors.append(f"net {name}: pin {pin!r} must be written REF.PAD")
                continue
            ref, pad = pin.split(".", 1)
            if ref not in refs:
                rep.errors.append(f"net {name}: unknown component {ref}")
                continue
            fp = fps.get(ref)
            if fp and pad not in fp.pad_names:
                rep.errors.append(f"net {name}: {ref} ({fp.lib_id}) has no pad {pad!r}; "
                                  f"its pads are {', '.join(fp.pad_names[:60])}")
                continue
            if pin in used and used[pin] != name:
                rep.errors.append(f"pin {pin} is in two nets: {used[pin]} and {name}")
            used[pin] = name

    for c in spec["components"]:
        ref = c.get("ref")
        fp = fps.get(ref)
        if not fp:
            continue
        nc = {str(x) for x in c.get("no_connect") or []}
        free = [p for p in fp.pad_names if f"{ref}.{p}" not in used and p not in nc]
        if free and not fp.lib_id.startswith("MountingHole"):
            rep.warnings.append(f"{ref}: pads {', '.join(free[:20])} are not connected "
                                f"(list them in no_connect if that is intended)")

    _check_fixed(spec, fps, rep, w, h)
    ie, iw = impedance.validate(spec)
    rep.errors += ie
    rep.warnings += iw
    me, mw = mains.validate(spec)
    rep.errors += me
    rep.warnings += mw
    fine = [c["ref"] for c in spec["components"] if FINE_PITCH.search(str(c.get("footprint") or ""))]
    if fine and not b.get("fiducials") and not b.get("panel"):
        rep.warnings.append(f"{', '.join(fine[:4])} have 0.5 mm pitch or finer: set board.fiducials true (three "
                            "fiducials in the corners) or order a panel (board.panel), whose rails carry them")

    board_area = w * h
    if b.get("outline"):
        board_area = abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(b["outline"], b["outline"][1:] + b["outline"][:1]))) / 2
        if len(b["outline"]) < 3:
            rep.errors.append("board.outline needs at least three points")
    if area > 0 and board_area < area * 1.8:
        rep.errors.append(f"board area {board_area:.0f} mm² is too small for parts that need about "
                          f"{area:.0f} mm² of courtyard; make it at least {area * 2.2:.0f} mm²")
    return rep


def hole_positions(spec: dict) -> list[tuple[float, float]]:
    """Mounting hole centres: the listed positions, or one in each corner."""
    b = spec["board"]
    mh = b.get("mounting_holes") or {}
    if not mh:
        return []
    if mh.get("positions"):
        return [(float(p[0]), float(p[1])) for p in mh["positions"]]
    w, h, inset = float(b["width_mm"]), float(b["height_mm"]), float(mh.get("inset_mm", 3.5))
    return [(inset, inset), (w - inset, inset), (w - inset, h - inset), (inset, h - inset)]


def hole_footprint(spec: dict) -> str:
    dia = float((spec["board"].get("mounting_holes") or {}).get("diameter_mm", 3.2))
    size = {2.2: "M2", 2.7: "M2.5", 3.2: "M3", 4.3: "M4"}.get(round(dia, 1))
    return f"MountingHole:MountingHole_{dia:g}mm" + (f"_{size}" if size else "")


def inside(poly: list, x: float, y: float) -> bool:
    hit = False
    for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


def edge_distance(poly: list, x: float, y: float) -> float:
    best = math.inf
    for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]):
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy or 1e-12)))
        best = min(best, math.hypot(x - ax - t * dx, y - ay - t * dy))
    return best


def pin_net_map(spec: dict) -> dict[str, str]:
    out = {}
    for n in normalise(spec)["nets"]:
        for p in n["pins"]:
            out[p] = n["name"]
    return out


def _rot(x: float, y: float, deg: float) -> tuple[float, float]:
    a = math.radians(deg)
    return x * math.cos(a) + y * math.sin(a), -x * math.sin(a) + y * math.cos(a)


def _box(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def on_bottom(c: dict) -> bool:
    """True for a part the spec puts on the bottom side of the board."""
    return str((c.get("placement") or {}).get("side") or "top").lower() in ("bottom", "back", "b.cu")


def _overlap(a, b, gap: float = 0.0) -> bool:
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


def _check_fixed(spec: dict, fps: dict, rep: Report, w: float, h: float) -> None:
    """Parts with a fixed placement: pads inside the board, no clashes with each other or holes."""
    edge = float(spec["rules"].get("edge_clearance_mm", 0.3))
    boxes: list[tuple[str, tuple]] = []
    sides: dict[str, str] = {}           # a through-hole part takes room on both sides
    from_kicad = {c.get("ref") for c in spec["components"] if (c.get("placement") or {}).get("from") == "kicad"}
    for c in spec["components"]:
        pl = c.get("placement") or {}
        fp = fps.get(c.get("ref"))
        if fp is None or pl.get("x_mm") is None or pl.get("y_mm") is None:
            continue
        x, y, r = float(pl["x_mm"]), float(pl["y_mm"]), float(pl.get("rotation") or 0)
        cx0, cy0, cx1, cy1 = fp.courtyard
        bottom = on_bottom(c)
        if bottom:                       # flipped: the footprint's y is mirrored before it is turned
            cy0, cy1 = -cy1, -cy0
        court = _box([(x + px, y + py) for px, py in (_rot(cx0, cy0, r), _rot(cx1, cy0, r),
                                                       _rot(cx1, cy1, r), _rot(cx0, cy1, r))])
        pads = []
        for p in fp.pads:
            if p.kind == "np_thru_hole":
                continue
            rr = max(p.w, p.h) / 2
            px, py = _rot(p.x, -p.y if bottom else p.y, r)
            pads.append((x + px - rr, y + py - rr, x + px + rr, y + py + rr))
        poly = spec["board"].get("outline")
        if pads and poly:
            bad = [(px0, py0) for b in pads for px0, py0 in ((b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3]))
                   if not inside(poly, px0, py0) or edge_distance(poly, px0, py0) < edge]
            if bad:
                rep.errors.append(f"{c['ref']} at ({x:g}, {y:g}) rot {r:g}: some of its pads are outside the board "
                                  f"outline or closer than {edge} mm to it (near {bad[0][0]:.1f}, {bad[0][1]:.1f}). "
                                  "Move it inward.")
        elif pads:
            pb = _box([(b[0], b[1]) for b in pads] + [(b[2], b[3]) for b in pads])
            if pb[0] < edge or pb[1] < edge or pb[2] > w - edge or pb[3] > h - edge:
                rep.errors.append(
                    f"{c['ref']} at ({x:g}, {y:g}) rot {r:g}: its pads span x {pb[0]:.2f}..{pb[2]:.2f}, "
                    f"y {pb[1]:.2f}..{pb[3]:.2f} mm, but copper must stay {edge} mm inside the "
                    f"{w:g}×{h:g} mm board. Move it inward.")
        if court[0] < -3 or court[1] < -3 or court[2] > w + 3 or court[3] > h + 3:
            rep.warnings.append(f"{c['ref']}: its courtyard reaches well past the board edge "
                                f"(x {court[0]:.1f}..{court[2]:.1f}, y {court[1]:.1f}..{court[3]:.1f} mm)")
        boxes.append((c["ref"], court))
        sides[c["ref"]] = "both" if any(p.kind in ("thru_hole", "np_thru_hole") for p in fp.pads) else (
            "bottom" if bottom else "top")
    mh = spec["board"].get("mounting_holes")
    if mh:
        rad = float(mh.get("diameter_mm", 3.2)) / 2 + 0.6
        poly = spec["board"].get("outline")
        for i, (mx, my) in enumerate(hole_positions(spec), start=1):
            if poly and (not inside(poly, mx, my) or edge_distance(poly, mx, my) < rad):
                rep.errors.append(f"mounting hole H{i} at ({mx:g}, {my:g}) is outside the board outline or too close "
                                  "to its edge; give mounting_holes.positions inside the outline")
            boxes.append((f"mounting hole H{i} at ({mx:g}, {my:g})", (mx - rad, my - rad, mx + rad, my + rad)))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            sa, sb = sides.get(a[0], "both"), sides.get(b[0], "both")
            if "both" not in (sa, sb) and sa != sb:
                continue                 # one on each side of the board
            if a[0].startswith("mounting") and b[0].startswith("mounting"):
                continue
            if _overlap(a[1], b[1]):
                if a[0] in from_kicad and b[0] in from_kicad:
                    rep.warnings.append(f"{a[0]} and {b[0]} overlap where they were placed in KiCad")
                    continue
                rep.errors.append(f"{a[0]} overlaps {b[0]}: move one of them (courtyards "
                                  f"{_fmt(a[1])} and {_fmt(b[1])} mm from the board's top-left corner)")


def _fmt(b) -> str:
    return f"x {b[0]:.1f}..{b[2]:.1f} y {b[1]:.1f}..{b[3]:.1f}"
