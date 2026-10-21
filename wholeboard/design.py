"""The design spec the AI writes, and its validation against the KiCad libraries.

A spec is plain JSON:

{
  "name": "usb_led_blinker",
  "summary": "one paragraph",
  "board": {"width_mm": 50, "height_mm": 30, "layers": 2, "thickness_mm": 1.6,
            "corner_radius_mm": 1.0, "mounting_holes": {"diameter_mm": 3.2, "inset_mm": 3.5},
            "ground_pour": "GND", "planes": {"In1.Cu": "GND", "In2.Cu": "+3V3"}},
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
    spec = dict(spec)
    spec.setdefault("name", "board")
    spec["name"] = re.sub(r"[^A-Za-z0-9_-]+", "_", str(spec["name"])).strip("_") or "board"
    b = dict(spec.get("board") or {})
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
    return spec


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
        if not c.get("lcsc") and not c.get("dnp") and not fid.startswith("MountingHole"):
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

    if area > 0 and w * h < area * 1.8:
        rep.errors.append(f"board area {w * h:.0f} mm² is too small for parts that need about "
                          f"{area:.0f} mm² of courtyard; make it at least {area * 2.2:.0f} mm²")
    return rep


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


def _overlap(a, b, gap: float = 0.0) -> bool:
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


def _check_fixed(spec: dict, fps: dict, rep: Report, w: float, h: float) -> None:
    """Parts with a fixed placement: pads inside the board, no clashes with each other or holes."""
    edge = float(spec["rules"].get("edge_clearance_mm", 0.3))
    boxes: list[tuple[str, tuple]] = []
    for c in spec["components"]:
        pl = c.get("placement") or {}
        fp = fps.get(c.get("ref"))
        if fp is None or pl.get("x_mm") is None or pl.get("y_mm") is None:
            continue
        x, y, r = float(pl["x_mm"]), float(pl["y_mm"]), float(pl.get("rotation") or 0)
        cx0, cy0, cx1, cy1 = fp.courtyard
        court = _box([(x + px, y + py) for px, py in (_rot(cx0, cy0, r), _rot(cx1, cy0, r),
                                                       _rot(cx1, cy1, r), _rot(cx0, cy1, r))])
        pads = []
        for p in fp.pads:
            if p.kind == "np_thru_hole":
                continue
            rr = max(p.w, p.h) / 2
            px, py = _rot(p.x, p.y, r)
            pads.append((x + px - rr, y + py - rr, x + px + rr, y + py + rr))
        if pads:
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
    mh = spec["board"].get("mounting_holes")
    if mh:
        inset = float(mh.get("inset_mm", 3.5))
        rad = float(mh.get("diameter_mm", 3.2)) / 2 + 0.6
        for i, (mx, my) in enumerate([(inset, inset), (w - inset, inset), (w - inset, h - inset),
                                      (inset, h - inset)], start=1):
            boxes.append((f"mounting hole H{i} at ({mx:g}, {my:g})", (mx - rad, my - rad, mx + rad, my + rad)))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if a[0].startswith("mounting") and b[0].startswith("mounting"):
                continue
            if _overlap(a[1], b[1]):
                rep.errors.append(f"{a[0]} overlaps {b[0]}: move one of them (courtyards "
                                  f"{_fmt(a[1])} and {_fmt(b[1])} mm from the board's top-left corner)")


def _fmt(b) -> str:
    return f"x {b[0]:.1f}..{b[2]:.1f} y {b[1]:.1f}..{b[3]:.1f}"
