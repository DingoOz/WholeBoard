"""Return paths: does a fast trace keep a plane under it the whole way?

A signal's return current flows in the plane next to its layer, right under the trace. Where the trace crosses a
void in that plane (a slot, a row of antipads, the edge of a pour) or a split between two planes of different nets,
the return current has to go round, the loop grows, and the trace radiates and picks up noise. After KiCad's DRC
has refilled the zones, this samples each controlled net's tracks every SAMPLE_MM and looks for the filled copper of
a ground or power zone on a neighbouring copper layer (either one, for an inner layer).

Limits, per impedance group: max_void_mm (1 mm) is the longest stretch with no plane under it; a split between two
planes of different nets is reported however short the gap. Places next to the net's own vias and through-hole pads
are skipped, since the plane is cleared round them on purpose.
"""

from __future__ import annotations

import math
import re

SAMPLE_MM = 0.2
MAX_VOID_MM = 1.0
_BIN = 0.5               # height of the bands a zone outline's edges are sorted into
_GND = re.compile(r"^(A|D|P|S|C)?GND\w*$|^VSS\w*$|^0V$", re.I)


def stack(geo: dict, layers: int) -> list[str]:
    """Copper layers from top to bottom."""
    n = max(layers, len(geo.get("layers") or []), 1)
    return ["F.Cu", *[f"In{i}.Cu" for i in range(1, n - 1)], "B.Cu"] if n > 1 else ["F.Cu"]


def neighbours(layer: str, order: list[str]) -> list[str]:
    if layer not in order:
        return []
    i = order.index(layer)
    return [order[j] for j in (i - 1, i + 1) if 0 <= j < len(order)]


class _Zone:
    """One filled polygon, with its edges sorted into horizontal bands for a quick point-in-polygon test. KiCad
    stores fills fractured (holes joined to the outline by zero-width cuts), so the even-odd rule handles holes."""

    def __init__(self, net: str, pts: list):
        self.net = net
        self.pts = [(float(x), float(y)) for x, y in pts]
        xs, ys = [p[0] for p in self.pts], [p[1] for p in self.pts]
        self.box = (min(xs), min(ys), max(xs), max(ys))
        self.bands: dict[int, list] = {}
        for a, b in zip(self.pts, self.pts[1:] + self.pts[:1]):
            if a[1] == b[1]:
                continue
            for k in range(int(min(a[1], b[1]) // _BIN), int(max(a[1], b[1]) // _BIN) + 1):
                self.bands.setdefault(k, []).append((a, b))

    def contains(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.box
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        inside = False
        for (ax, ay), (bx, by) in self.bands.get(int(y // _BIN), ()):
            if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
                inside = not inside
        return inside


def _samples(t: dict) -> list[tuple[float, float]]:
    pts = [t["a"], t["m"], t["b"]] if "m" in t else [t["a"], t["b"]]   # an arc as two chords
    out = []
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        n = max(1, math.ceil(math.dist((ax, ay), (bx, by)) / SAMPLE_MM))
        out += [(ax + (bx - ax) * i / n, ay + (by - ay) * i / n) for i in range(n)]
    out.append(tuple(pts[-1]))
    return out


def check(spec: dict, geo: dict, groups: list[dict]) -> dict:
    """Voids and splits in the reference plane under each controlled net. groups: impedance.rules(spec)."""
    planes = set((spec["board"].get("planes") or {}).values())
    refs = {n["name"] for n in spec.get("nets") or [] if n.get("class") == "power" or _GND.match(n["name"])}
    refs |= planes | {spec["board"].get("ground_pour")}
    zones: dict[str, list[_Zone]] = {}
    for z in geo.get("zones") or []:
        if z.get("net") in refs and len(z.get("pts") or []) >= 3:
            zones.setdefault(z["layer"], []).append(_Zone(z["net"], z["pts"]))
    out = {"nets": [], "warnings": [], "note": ""}
    if not zones:
        out["note"] = "No ground or power zones were found on the board, so the return paths were not checked."
        return out
    order = stack(geo, int(spec["board"].get("layers") or 2))
    limits = {}
    for g in groups:
        for n in g["nets"]:
            limits[n] = float(g.get("max_void_mm") or MAX_VOID_MM)
    keep_out: dict[str, list[tuple[float, float, float]]] = {}       # (x, y, radius) round the net's own holes
    for v in geo.get("vias") or []:
        if v.get("net") in limits and v.get("p"):
            keep_out.setdefault(v["net"], []).append((v["p"][0], v["p"][1], v.get("d", 0.6) / 2 + 0.3))
    for p in geo.get("pads") or []:
        if p.get("net") in limits and p.get("drill"):
            keep_out.setdefault(p["net"], []).append((p["x"], p["y"], max(p["w"], p["h"]) / 2 + 0.3))
    no_plane: set[str] = set()
    for net, limit in limits.items():
        tracks = [t for t in (geo.get("tracks") or []) + (geo.get("arcs") or []) if t.get("net") == net]
        if not tracks:
            continue
        worst, worst_at, splits, checked = 0.0, None, set(), 0.0
        for t in tracks:
            below = {lay: zones[lay] for lay in neighbours(t.get("layer", ""), order) if zones.get(lay)}
            if not below:
                no_plane.add(t.get("layer", "?"))
                continue
            gap, gap_at, last = 0.0, None, set()
            prev = None
            for x, y in _samples(t):
                step = math.dist(prev, (x, y)) if prev else 0.0
                prev = (x, y)
                checked += step
                if any(math.hypot(x - hx, y - hy) <= r for hx, hy, r in keep_out.get(net, ())):
                    gap, gap_at = 0.0, None
                    continue
                # the planes under this point: (layer, net) on each neighbouring layer that has copper here
                here = {(lay, z.net) for lay, zs in below.items() for z in zs if z.contains(x, y)}
                if not here:
                    gap += step
                    gap_at = gap_at or (round(x, 2), round(y, 2))
                    if gap > worst:
                        worst, worst_at = gap, gap_at
                    continue
                # a split: no neighbouring layer carries the same plane from the last covered point to this one
                if last and last.isdisjoint(here):
                    a, b = sorted({n for _, n in last})[0], sorted({n for _, n in here})[0]
                    if a != b:
                        splits.add(tuple(sorted((a, b))) + ((round(x, 2), round(y, 2)),))
                last, gap, gap_at = here, 0.0, None
        if not checked:
            continue                    # none of its tracks has a plane next to it (said once, in the note)
        row = {"net": net, "checked_mm": round(checked, 1), "longest_void_mm": round(worst, 2),
               "void_at": worst_at, "limit_mm": limit,
               "splits": [{"between": [a, b], "at": at} for a, b, at in sorted(splits)]}
        out["nets"].append(row)
        if worst > limit:
            out["warnings"].append(f"{net} runs {worst:.1f} mm with no plane under it (near {worst_at[0]:g}, "
                                   f"{worst_at[1]:g}); its return current has to go round. Keep the plane solid "
                                   f"under it or move the trace (limit {limit:g} mm)")
        for s in row["splits"][:3]:
            out["warnings"].append(f"{net} crosses from the {s['between'][0]} plane to the {s['between'][1]} plane "
                                   f"(near {s['at'][0]:g}, {s['at'][1]:g}); route it over one plane, or put a "
                                   "stitching capacitor between them next to the crossing")
    if no_plane:
        out["note"] = (f"No ground or power zone next to {', '.join(sorted(no_plane))}, so the controlled tracks there "
                       "were not checked.")
    return out
