"""Controlled-impedance traces: widths and gaps from JLCPCB's layer stack, and a length check after routing.

A design lists its controlled nets in "impedance":
  [{"nets": ["USB_DP", "USB_DN"], "ohms": 90, "kind": "diff", "max_skew_mm": 1.25},
   {"nets": ["RF_ANT"], "ohms": 50, "kind": "single"}]

Widths use the IPC-2141 microstrip formulas for an outer-layer trace over the nearest plane. They are
approximate (the coupled-pair formula most of all): order 4 and 6 layer boards with impedance control and
the stack-up named here (free at JLCPCB), and check the figures in JLCPCB's impedance calculator.
"""

from __future__ import annotations

import math
import re

# Outer layer to the nearest plane: dielectric height (mm) and relative permittivity.
STACKUPS = {
    2: {"name": "2-layer FR-4", "h": None, "er": 4.5},                          # h = board thickness - copper
    4: {"name": "JLC04161H-7628", "h": 0.2104, "er": 4.4},
    6: {"name": "JLC06161H-2116", "h": 0.1164, "er": 4.16},
}
COPPER_MM = 0.035
MIN_TRACK, MIN_GAP = 0.127, 0.127


def stackup(layers: int, thickness: float = 1.6) -> dict:
    s = dict(STACKUPS.get(layers) or STACKUPS[4])
    if s["h"] is None:
        s["h"] = max(thickness - 2 * COPPER_MM, 0.2)
    return s


def microstrip(w: float, h: float, er: float, t: float = COPPER_MM) -> float:
    """Single-ended impedance of an outer-layer trace (IPC-2141)."""
    return 87 / math.sqrt(er + 1.41) * math.log(5.98 * h / (0.8 * w + t))


def diff_microstrip(w: float, s: float, h: float, er: float, t: float = COPPER_MM) -> float:
    return 2 * microstrip(w, h, er, t) * (1 - 0.48 * math.exp(-0.96 * s / h))


def _solve(fn, target: float, lo: float = 0.05, hi: float = 6.0) -> float:
    """Impedance falls as the width grows: bisect for the width that gives target."""
    for _ in range(60):
        mid = (lo + hi) / 2
        if fn(mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def design_rule(group: dict, layers: int, thickness: float, clearance: float) -> dict:
    """Width (and gap) for one impedance group, and whether it is practical on this stack-up."""
    st = stackup(layers, thickness)
    h, er = st["h"], st["er"]
    ohms = float(group.get("ohms") or (90 if group.get("kind") == "diff" else 50))
    out = {"nets": list(group.get("nets") or []), "kind": group.get("kind", "single"), "ohms": ohms,
           "stackup": st["name"], "h_mm": round(h, 4), "er": er}
    if out["kind"] == "diff":
        gap = max(float(group.get("gap_mm") or 0) or max(clearance, MIN_GAP, round(h * 0.8, 3)), MIN_GAP)
        w = _solve(lambda x: diff_microstrip(x, gap, h, er), ohms)
        out.update(width_mm=round(w, 3), gap_mm=round(gap, 3), achieved=round(diff_microstrip(w, gap, h, er), 1),
                   max_skew_mm=float(group.get("max_skew_mm") or 1.25))
    else:
        w = _solve(lambda x: microstrip(x, h, er), ohms)
        out.update(width_mm=round(w, 3), achieved=round(microstrip(w, h, er), 1))
    out["practical"] = MIN_TRACK <= out["width_mm"] <= 0.6
    if out["width_mm"] < MIN_TRACK:
        out["note"] = f"{ohms:g} Ω needs a {out['width_mm']} mm track, below JLCPCB's {MIN_TRACK} mm minimum"
    elif out["width_mm"] > 0.6:
        out["note"] = (f"{ohms:g} Ω on this {st['name']} stack-up needs {out['width_mm']} mm tracks (the plane is "
                       f"{h:.2f} mm away), which will not fit between the pads. "
                       + ("For USB full speed (12 Mbit/s) that does not matter: keep the pair short, together and "
                          "over the ground pour. For USB high speed, Ethernet or RF use a 4-layer board."
                          if layers == 2 else "Use a thinner dielectric stack-up."))
    return out


def rules(spec: dict) -> list[dict]:
    b, r = spec["board"], spec["rules"]
    return [design_rule(g, int(b["layers"]), float(b.get("thickness_mm") or 1.6), float(r["clearance_mm"]))
            for g in spec.get("impedance") or [] if isinstance(g, dict)]


def validate(spec: dict) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    names = {n["name"] for n in spec.get("nets") or []}
    for i, g in enumerate(spec.get("impedance") or []):
        if not isinstance(g, dict) or not g.get("nets"):
            errors.append(f"impedance[{i}] needs a list of nets")
            continue
        for n in g["nets"]:
            if n not in names:
                errors.append(f"impedance[{i}]: there is no net {n}")
        if g.get("kind") == "diff":
            if len(g["nets"]) != 2:
                errors.append(f"impedance[{i}]: a differential pair has exactly two nets")
            elif not pair_names(*g["nets"]):
                warnings.append(f"impedance: {g['nets'][0]}/{g['nets'][1]} are not named as a pair (same name ending "
                                "in P/N or +/-), so TraceMaker routes them separately; rename them, e.g. USB_DP/USB_DN")
    return errors, warnings


def pair_names(a: str, b: str) -> bool:
    if len(a) != len(b) or a[:-1] != b[:-1]:
        return False
    return {a[-1], b[-1]} in ({"P", "N"}, {"+", "-"})


def netclasses(spec: dict) -> list[dict]:
    """Extra KiCad net classes for the controlled nets: (name, width, gap, nets)."""
    out = []
    for i, g in enumerate(rules(spec)):
        w = g["width_mm"] if g["practical"] else None
        out.append({"name": f"Impedance{i + 1}_{g['ohms']:g}R", "width": w, "gap": g.get("gap_mm"), "nets": g["nets"],
                    "diff": g["kind"] == "diff"})
    return out


def has_pairs(spec: dict) -> bool:
    return any(g.get("kind") == "diff" and len(g.get("nets") or []) == 2 and pair_names(*g["nets"])
               for g in spec.get("impedance") or [] if isinstance(g, dict))


def _length(geo: dict, net: str) -> tuple[float, list[float]]:
    total, widths = 0.0, []
    for t in geo.get("tracks") or []:
        if t["net"] == net:
            total += math.dist(t["a"], t["b"])
            widths.append(t["w"])
    for a in geo.get("arcs") or []:
        if a["net"] == net:
            total += _arc_len(a["a"], a["m"], a["b"])
            widths.append(a["w"])
    return total, widths


def _arc_len(a, m, b) -> float:
    ax, ay = a; bx, by = m; cx, cy = b
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return math.dist(a, b)
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.dist((ux, uy), a)
    ang = lambda p: math.atan2(p[1] - uy, p[0] - ux)  # noqa: E731
    t0, t1, t2 = ang(a), ang(m), ang(b)
    sweep = (t2 - t0) % (2 * math.pi)
    if not (0 <= (t1 - t0) % (2 * math.pi) <= sweep):
        sweep = 2 * math.pi - sweep
    return r * sweep


def check(spec: dict, geo: dict) -> dict:
    """After routing: each controlled net's length and narrowest track, and each pair's skew."""
    groups, problems, warnings = [], [], []
    vias = {}
    for v in geo.get("vias") or []:
        vias[v["net"]] = vias.get(v["net"], 0) + 1
    for g in rules(spec):
        rows = []
        for n in g["nets"]:
            length, widths = _length(geo, n)
            rows.append({"net": n, "length_mm": round(length, 2), "vias": vias.get(n, 0),
                         "width_mm": round(min(widths), 3) if widths else None})
        row = {**g, "members": rows}
        if g["kind"] == "diff" and len(rows) == 2 and rows[0]["length_mm"] and rows[1]["length_mm"]:
            row["skew_mm"] = round(abs(rows[0]["length_mm"] - rows[1]["length_mm"]), 2)
            if row["skew_mm"] > g["max_skew_mm"]:
                warnings.append(f"{rows[0]['net']}/{rows[1]['net']} differ in length by {row['skew_mm']} mm "
                                f"(limit {g['max_skew_mm']} mm); add a small meander to the shorter one in KiCad")
            if rows[0]["vias"] != rows[1]["vias"]:
                warnings.append(f"{rows[0]['net']} has {rows[0]['vias']} vias but {rows[1]['net']} has {rows[1]['vias']}")
        if g["practical"]:
            for m in rows:
                if m["width_mm"] and abs(m["width_mm"] - g["width_mm"]) > 0.02:
                    warnings.append(f"{m['net']} is routed {m['width_mm']} mm wide in places; {g['ohms']:g} Ω needs "
                                    f"{g['width_mm']} mm")
        elif g.get("note"):
            warnings.append(g["note"])
        groups.append(row)
    return {"groups": groups, "problems": problems, "warnings": warnings,
            "order_note": (f"Order with impedance control, stack-up {groups[0]['stackup']}."
                           if groups and int(spec["board"]["layers"]) >= 4 else "")}


DOC = re.sub(r"\s+", " ", __doc__.split("\n\n", 1)[1]).strip()
