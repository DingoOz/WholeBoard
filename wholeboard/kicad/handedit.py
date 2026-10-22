"""Keep the changes a user makes in KiCad to a finished board.

After a build the board is <name>.kicad_pcb, the routed board with its ground pour. When the user moves parts or
routes tracks in KiCad and saves it, the next build reads the changes back:

- Parts moved by hand become fixed placements in the spec (locked, so the placer leaves them).
- Nets whose copper was changed by hand keep that copper: it goes into the new board as locked tracks and vias,
  and TraceMaker routes only what is still unconnected. The parts those tracks reach are held in place too.

What has been kept so far lives in the project's state ("kicad_edits"), so the edits survive every later build
until the user lets them go. Other changes (the outline, zones, text) are not read back; the build says so.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

from . import geometry

ORIGIN = (100.0, 100.0)       # the board's top-left in KiCad coordinates, as board.py writes it
TOL = 0.005                   # mm: smaller moves are rounding, not edits


def fingerprint(pcb: Path) -> str:
    return hashlib.sha256(pcb.read_bytes()).hexdigest()


def _r(v: float) -> float:
    return round(float(v), 3)


def _seg_key(t: dict) -> tuple:
    a, b = (_r(t["a"][0]), _r(t["a"][1])), (_r(t["b"][0]), _r(t["b"][1]))
    return ("t", t["layer"], _r(t["w"]), *sorted([a, b]))


def _arc_key(t: dict) -> tuple:
    a, b = (_r(t["a"][0]), _r(t["a"][1])), (_r(t["b"][0]), _r(t["b"][1]))
    return ("a", t["layer"], _r(t["w"]), _r(t["m"][0]), _r(t["m"][1]), *sorted([a, b]))


def _via_key(v: dict) -> tuple:
    return ("v", _r(v["p"][0]), _r(v["p"][1]), _r(v["d"]), _r(v["drill"]))


def _copper(geo: dict) -> dict[str, set]:
    by: dict[str, set] = {}
    for t in geo.get("tracks") or []:
        by.setdefault(t.get("net") or "", set()).add(_seg_key(t))
    for t in geo.get("arcs") or []:
        by.setdefault(t.get("net") or "", set()).add(_arc_key(t))
    for v in geo.get("vias") or []:
        by.setdefault(v.get("net") or "", set()).add(_via_key(v))
    return by


def _in_pad(x: float, y: float, p: dict, margin: float = 0.05) -> bool:
    a = math.radians(-float(p.get("rot") or 0))
    dx, dy = x - p["x"], y - p["y"]
    lx, ly = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
    return abs(lx) <= p["w"] / 2 + margin and abs(ly) <= p["h"] / 2 + margin


def _shape(outline: list | None) -> list:
    """An outline's pieces in a fixed order (KiCad reorders them when it saves)."""
    def key(o: dict) -> tuple:
        pts = tuple(sorted((_r(v[0]), _r(v[1])) for k, v in o.items() if isinstance(v, list) and len(v) == 2))
        return (o.get("kind"), pts)
    return sorted(key(o) for o in outline or [])


def diff(before: dict, after: dict) -> dict:
    """What changed between the board WholeBoard routed and the one saved from KiCad.

    Returns {"moved": {ref: placement}, "held": {ref: placement}, "nets": {net: copper}, "cleared": [net],
    "removed": [ref], "other": [notes]}. Placements and copper are in the spec's board coordinates."""
    fb = {f["ref"]: f for f in before.get("footprints") or []}
    fa = {f["ref"]: f for f in after.get("footprints") or []}

    def place(f: dict) -> dict:
        return {"x_mm": round(f["x"] - ORIGIN[0], 4), "y_mm": round(f["y"] - ORIGIN[1], 4),
                "rotation": round(float(f.get("rot") or 0), 3) % 360,
                "side": "bottom" if f.get("side") == "B.Cu" else "top", "locked": True, "from": "kicad",
                "footprint": f.get("lib")}

    moved = {}
    for ref, f in fa.items():
        g = fb.get(ref)
        if g and (abs(f["x"] - g["x"]) > TOL or abs(f["y"] - g["y"]) > TOL
                  or abs(((f.get("rot") or 0) - (g.get("rot") or 0) + 180) % 360 - 180) > 0.01
                  or f.get("side") != g.get("side")):
            moved[ref] = place(f)
    cb, ca = _copper(before), _copper(after)
    changed = {n for n in set(cb) | set(ca) if n and cb.get(n, set()) != ca.get(n, set())}
    nets, cleared = {}, []
    for net in sorted(changed):
        tracks = [t for t in after.get("tracks") or [] if t.get("net") == net]
        arcs = [t for t in after.get("arcs") or [] if t.get("net") == net]
        vias = [v for v in after.get("vias") or [] if v.get("net") == net]
        if not (tracks or arcs or vias):
            cleared.append(net)
            continue
        sh = lambda p: [round(p[0] - ORIGIN[0], 4), round(p[1] - ORIGIN[1], 4)]  # noqa: E731
        nets[net] = {"tracks": [{"a": sh(t["a"]), "b": sh(t["b"]), "w": t["w"], "layer": t["layer"]} for t in tracks],
                     "arcs": [{"a": sh(t["a"]), "m": sh(t["m"]), "b": sh(t["b"]), "w": t["w"], "layer": t["layer"]}
                              for t in arcs],
                     "vias": [{"p": sh(v["p"]), "d": v["d"], "drill": v["drill"]} for v in vias]}
    # the parts the kept copper reaches stay where they are
    held = {}
    ends = [(t["a"], t["net"]) for t in (after.get("tracks") or []) + (after.get("arcs") or []) if t.get("net") in nets]
    ends += [(t["b"], t["net"]) for t in (after.get("tracks") or []) + (after.get("arcs") or []) if t.get("net") in nets]
    for p in after.get("pads") or []:
        if p["ref"] in moved or p["ref"] in held or p.get("net") not in nets or p["ref"] not in fa:
            continue
        if any(n == p["net"] and _in_pad(e[0], e[1], p) for e, n in ends):
            held[p["ref"]] = place(fa[p["ref"]])
    other = []
    if _shape(after.get("outline")) != _shape(before.get("outline")):
        other.append("the board outline")
    added = sorted(set(fa) - set(fb))
    if added:
        other.append("parts added in KiCad (" + ", ".join(added[:5]) + "); add them to the design instead")
    return {"moved": moved, "held": held, "nets": nets, "cleared": cleared, "removed": sorted(set(fb) - set(fa)),
            "other": other}


def detect(artifacts: dict) -> dict | None:
    """The hand edits in the last finished board, or None when it was not changed since WholeBoard wrote it."""
    pcb = Path((artifacts or {}).get("pcb") or "")
    if not pcb.is_file():
        return None
    if artifacts.get("pcb_sha") and fingerprint(pcb) == artifacts["pcb_sha"]:
        return None
    routed = pcb.with_name(f"{pcb.stem}-3-routed.kicad_pcb")
    if not routed.is_file():
        return None
    d = diff(geometry.read(routed), geometry.read(pcb))
    return d if d["moved"] or d["nets"] or d["cleared"] else None


def merge(kept: dict | None, d: dict) -> dict:
    """Add newly found edits to the ones already kept: newer placements and copper win."""
    kept = {"parts": dict((kept or {}).get("parts") or {}), "nets": dict((kept or {}).get("nets") or {})}
    for ref, pl in {**d["held"], **d["moved"]}.items():
        if ref in d["moved"] or ref not in kept["parts"]:
            kept["parts"][ref] = {**pl, "hand": ref in d["moved"] or (kept["parts"].get(ref) or {}).get("hand", False)}
    for net in d["cleared"]:
        kept["nets"].pop(net, None)
    kept["nets"].update(d["nets"])
    return kept


def apply(spec: dict, kept: dict | None) -> tuple[dict, list[str]]:
    """Put the kept edits into a spec. Returns the spec and notes on what could not be kept."""
    if not kept or spec.get("keep_kicad_edits") is False:
        return spec, []
    spec = {**spec, "components": [dict(c) for c in spec.get("components") or []]}
    comps = {c.get("ref"): c for c in spec["components"]}
    notes, placed = [], set()
    for ref, pl in (kept.get("parts") or {}).items():
        c = comps.get(ref)
        if not c:
            notes.append(f"{ref} was placed by hand but is no longer in the design")
            continue
        if pl.get("footprint") and c.get("footprint") and pl["footprint"] != c["footprint"]:
            notes.append(f"{ref} has a new footprint, so its hand placement was dropped")
            continue
        c["placement"] = {k: pl[k] for k in ("x_mm", "y_mm", "rotation", "side", "locked")} | {"from": "kicad"}
        placed.add(ref)
    nets = {n.get("name"): n for n in spec.get("nets") or []}
    copper = []
    for net, cu in (kept.get("nets") or {}).items():
        if net not in nets:
            notes.append(f"the hand-routed net {net} is no longer in the design, so its tracks were dropped")
            continue
        copper.append({"net": net, **cu})
    if copper:
        spec["fixed_copper"] = copper
    return spec, notes


def summary(kept: dict | None) -> dict:
    parts = (kept or {}).get("parts") or {}
    return {"hand_placed": sorted(r for r, p in parts.items() if p.get("hand")),
            "held": sorted(r for r, p in parts.items() if not p.get("hand")),
            "nets": sorted((kept or {}).get("nets") or {})}


def describe(kept: dict | None) -> str:
    s = summary(kept)
    bits = []
    if s["hand_placed"]:
        bits.append(f"{len(s['hand_placed'])} hand-placed part{'s' if len(s['hand_placed']) != 1 else ''}")
    if s["nets"]:
        bits.append(f"{len(s['nets'])} routed net{'s' if len(s['nets']) != 1 else ''} "
                    f"({', '.join(s['nets'][:4])}{'…' if len(s['nets']) > 4 else ''})")
    text = "Kept " + " and ".join(bits) + " from KiCad" if bits else ""
    if s["held"]:
        text += f"; {len(s['held'])} more part{'s are' if len(s['held']) != 1 else ' is'} held where the kept tracks reach them"
    return text
