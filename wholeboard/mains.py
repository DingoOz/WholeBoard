"""Mains safety: creepage, clearance, a fuse and an isolation barrier for boards that touch mains.

A design that connects to mains declares it in "mains":
  {"nets": ["AC_L", "AC_N"], "volts": 230, "isolated": true}
volts is the working voltage (rms for AC; default 250, which covers 230 and 240 V mains). isolated true (the
default) means the low-voltage side is meant to be safe to touch (SELV): it must be separated from everything on
the mains side by reinforced insulation, through an isolation barrier part: an isolated supply module (HLK-PM01,
IRM-02), a transformer, an optocoupler or digital isolator, or a relay; mark any other part that crosses the
barrier (a Y capacitor) with "barrier": true. isolated false means the whole board is live (a capacitive dropper,
a non-isolated buck): it must never be touched while plugged in, and only the mains nets' own spacing is checked.
The mains side is every net reached from the mains nets through parts that are not barrier parts.

Requirements, for pollution degree 2 and FR-4 (material group IIIb), overvoltage category II (overvoltage_category
3 for fixed wiring):
  - between the listed mains nets (live and neutral, before and after the fuse, the rectified bus): IPC-2221 table
    6-1 B2 (outer layers) and B1 (inner) for the peak voltage (functional insulation). List every net that carries
    the full mains voltage; other mains-side nets (an LED feed, a controller's supply) are not held to it;
  - from the mains side to the low-voltage side, mounting holes and anything else: IEC 62368-1 / IEC 60664-1
    reinforced insulation: clearance for the next mains transient step up (3.0 mm at 230 V), creepage twice the
    basic value (5.0 mm at 250 V). Creepage runs along the board surface, so a milled slot (at least 1 mm wide)
    makes it longer: WholeBoard cuts one under each barrier part whose pins are closer than that, unless the part
    says "slot": false.
WholeBoard refuses to finish a mains design with no fuse on the mains input, no isolation barrier (when isolated)
or a spacing that fails, and says why. These checks are an aid, not a certification: have a mains design
reviewed, and build it into an enclosure that keeps fingers out.
"""

from __future__ import annotations

import heapq
import math
import re

from .kicad.geometry import _rot

DEFAULT_VOLTS = 250.0
TOL = 0.01                       # mm
SLOT_W = 1.0                     # mm, JLCPCB's narrowest routed slot; also IEC 62368-1's X for pollution degree 2
# IEC 60664-1 table F.1: rated impulse voltage for a working voltage (V rms, line to neutral), by overvoltage category
IMPULSE = [(50, (330, 500, 800)), (100, (500, 800, 1500)), (150, (800, 1500, 2500)), (300, (1500, 2500, 4000)),
           (600, (2500, 4000, 6000)), (1000, (4000, 6000, 8000))]
STEPS = [330, 500, 800, 1500, 2500, 4000, 6000, 8000, 12000]
# IEC 60664-1 table F.2, inhomogeneous field, pollution degree 2: clearance (mm) for an impulse voltage (V)
CLEAR_IMPULSE = {330: 0.2, 500: 0.2, 800: 0.2, 1500: 0.5, 2500: 1.5, 4000: 3.0, 6000: 5.5, 8000: 8.0, 12000: 14.0}
# IEC 60664-1 table F.4, pollution degree 2, material group III: basic creepage (mm) for a working voltage (V rms)
CREEP = [(50, 1.2), (63, 1.25), (80, 1.3), (100, 1.4), (125, 1.5), (160, 1.6), (200, 2.0), (250, 2.5), (320, 3.2),
         (400, 4.0), (500, 5.0), (630, 6.3), (800, 8.0), (1000, 10.0)]
# IPC-2221 table 6-1: conductor spacing (mm) for a peak voltage; B1 internal layers, B2 external uncoated
IPC_B1 = [(15, 0.05), (30, 0.05), (50, 0.1), (100, 0.1), (150, 0.2), (170, 0.2), (250, 0.2), (300, 0.2), (500, 0.25)]
IPC_B2 = [(15, 0.1), (30, 0.1), (50, 0.6), (100, 0.6), (150, 0.6), (170, 1.25), (250, 1.25), (300, 1.25), (500, 2.5)]

MAINS_NAME = re.compile(r"^(AC_?(L|N|LINE|LIVE|NEUTRAL|IN)\w*|LINE|LIVE|NEUTRAL|MAINS\w*|\d{3}V_?AC|VAC\w*)$", re.I)
MAINS_PART = re.compile(r"HLK-?PM|IRM-?\d|VARISTOR|MOV|Fuseholder|Converter_ACDC|X2|275V|250VAC", re.I)
BARRIER = re.compile(r"HLK-?PM|HLK-?\d|IRM-?\d|TRANSFORMER|PC8\d\d|LTV-?\d|EL3?8\d\d|TLP\d|6N13\d|H11\w|MOC30|"
                     r"FOD\w|ADUM|ISO7\d|ISO1\d|SI86|SI87|CA-IS|NSI\d|OPTO|RELAY|HF\d+F|SRD-|G5LE|G5V", re.I)
FUSE = re.compile(r"FUSE|^F\d|MF-?R|PPTC|POLYFUSE", re.I)


def _table(rows: list, v: float, per_volt: float | None = None) -> float:
    for lim, val in rows:
        if v <= lim:
            return val
    return v * per_volt if per_volt else rows[-1][1]


def config(spec: dict) -> dict | None:
    """The design's mains settings, with nets of class "hv" added, or None if it has no mains."""
    m = spec.get("mains")
    hv = [n["name"] for n in spec.get("nets") or [] if str(n.get("class") or "").lower() == "hv"]
    if not m and not hv:
        return None
    m = dict(m) if isinstance(m, dict) else {}
    nets = list(dict.fromkeys(list(m.get("nets") or []) + hv))
    return {"nets": nets, "volts": float(m.get("volts") or DEFAULT_VOLTS), "kind": m.get("kind", "ac"),
            "isolated": m.get("isolated", True) is not False, "overvoltage_category": int(m.get("overvoltage_category") or 2)}


def requirements(cfg: dict) -> dict:
    v = cfg["volts"]
    peak = v * math.sqrt(2) if cfg["kind"] == "ac" else v
    cat = min(max(cfg["overvoltage_category"], 1), 3) - 1
    impulse = next((imp[cat] for lim, imp in IMPULSE if v <= lim), IMPULSE[-1][1][cat])
    reinforced_impulse = STEPS[min(STEPS.index(impulse) + 1, len(STEPS) - 1)]
    basic_creep = _table(CREEP, v, CREEP[-1][1] / CREEP[-1][0])    # the next row up, not interpolated
    return {"working_v": v, "peak_v": round(peak), "impulse_v": impulse,
            "functional_outer_mm": round(_table(IPC_B2, peak, 0.005), 2),
            "functional_inner_mm": round(_table(IPC_B1, peak, 0.0025), 2),
            "clearance_mm": max(CLEAR_IMPULSE[reinforced_impulse], _table(IPC_B2, peak, 0.005)),
            "basic_creepage_mm": round(basic_creep, 2),
            "creepage_mm": round(max(2 * basic_creep, CLEAR_IMPULSE[reinforced_impulse]), 2)}


def is_barrier(c: dict) -> bool:
    if c.get("barrier") is not None:
        return bool(c["barrier"])
    ref = str(c.get("ref", ""))
    text = " ".join(str(c.get(k) or "") for k in ("value", "mpn", "description", "footprint"))
    return ref[:1] in ("T", "K") and not ref.startswith("TP") or bool(BARRIER.search(text))


def sides(spec: dict, cfg: dict) -> tuple[set[str], dict[str, list[str]]]:
    """The mains-side nets (reached from the mains nets through non-barrier parts), and each barrier part's pins."""
    comps = {c["ref"]: c for c in spec.get("components") or []}
    by_ref: dict[str, set[str]] = {}
    for n in spec.get("nets") or []:
        for pin in n.get("pins") or []:
            by_ref.setdefault(pin.split(".", 1)[0], set()).add(n["name"])
    hot, todo = set(cfg["nets"]), list(cfg["nets"])
    barriers = {r for r, c in comps.items() if is_barrier(c)}
    while todo:
        net = todo.pop()
        for ref, nets in by_ref.items():
            if ref not in barriers and net in nets:
                for n in nets - hot:
                    hot.add(n)
                    todo.append(n)
    crossing = {r: sorted(by_ref.get(r, set())) for r in barriers
                if by_ref.get(r, set()) & hot and by_ref.get(r, set()) - hot}
    return hot, crossing


def validate(spec: dict) -> tuple[list[str], list[str]]:
    """Before building: refuse a mains design with no fuse or no isolation barrier."""
    errors, warnings = [], []
    cfg = config(spec)
    names = {n["name"] for n in spec.get("nets") or []}
    if not cfg:
        guess = sorted(n for n in names if MAINS_NAME.match(n))
        parts = [c["ref"] for c in spec.get("components") or [] if MAINS_PART.search(
            " ".join(str(c.get(k) or "") for k in ("value", "footprint", "description", "mpn")))]
        if guess:
            (errors if parts else warnings).append(
                f"Nets {', '.join(guess)} look like mains" + (f" (and {', '.join(parts[:4])} are mains parts)" if parts else "")
                + ". Declare them in \"mains\" ({\"nets\": [...], \"volts\": 230}) so WholeBoard checks creepage, "
                "clearance, the fuse and the isolation barrier; if they are not mains, rename them.")
        elif parts:
            warnings.append(f"{', '.join(parts[:4])} look like mains parts; if the board connects to mains, declare "
                            "its mains nets in \"mains\".")
        return errors, warnings
    for n in cfg["nets"]:
        if n not in names:
            errors.append(f"mains: there is no net {n}")
    if errors:
        return errors, warnings
    comps = spec.get("components") or []
    pins = {n["name"]: {p.split(".", 1)[0] for p in n.get("pins") or []} for n in spec.get("nets") or []}
    fuses = [c["ref"] for c in comps if FUSE.search(str(c.get("ref", ""))[:2] + " " + str(c.get("value") or "") + " "
                                                   + str(c.get("description") or ""))
             and any(c["ref"] in pins.get(n, ()) for n in cfg["nets"])]
    if not fuses:
        errors.append(f"Mains design with no fuse: put a fuse (ref F1, e.g. a 5x20 mm or TR5 slow-blow fuse sized "
                      f"for the load) in series with the live input, on one of {', '.join(cfg['nets'])}.")
    hot, crossing = sides(spec, cfg)
    cold = names - hot
    if cfg["isolated"]:
        if not cold:
            errors.append("Mains design with no isolation barrier: every net connects to mains through ordinary "
                          "parts. Use an isolated supply module (e.g. HLK-PM01), a transformer or optocouplers "
                          "between the mains side and the low-voltage side, or set mains.isolated false if the "
                          "whole board is meant to be live (then it must be fully enclosed).")
        elif not crossing:
            errors.append("Mains design with no isolation barrier part between the mains side and "
                          f"{', '.join(sorted(cold)[:6])}; mark the isolating part with \"barrier\": true.")
    else:
        warnings.append("mains.isolated is false: the whole board is at mains potential. It must be in a closed "
                        "insulating enclosure, with no exposed connectors or metal, and never probed while plugged in.")
    if any(str(c.get("ref", "")).startswith(("H", "MH")) for c in comps) and cfg["isolated"]:
        warnings.append("Mounting holes are kept the reinforced distance from the mains side; use nylon screws "
                        "if they come closer.")
    return errors, warnings


# ---------------------------------------------------------------- geometry
# Every copper item becomes a set of segments with a radius: a pad's outline, a track, a via, a zone's outline.

def _pad_shape(p: dict) -> tuple[list, float]:
    w, h = float(p.get("w") or 0), float(p.get("h") or 0)
    if p.get("shape") == "circle" or (p.get("shape") == "oval" and abs(w - h) < 1e-6):
        return [((p["x"], p["y"]), (p["x"], p["y"]))], max(w, h) / 2
    if p.get("shape") == "oval":
        r = min(w, h) / 2
        a = (w / 2 - r, 0) if w > h else (0, h / 2 - r)
        e = [_rot(a[0], a[1], p.get("rot") or 0), _rot(-a[0], -a[1], p.get("rot") or 0)]
        return [((p["x"] + e[0][0], p["y"] + e[0][1]), (p["x"] + e[1][0], p["y"] + e[1][1]))], r
    # a rounded rectangle is the rectangle inside its corner arcs, grown by their radius
    r = float(p.get("rratio") or 0) * min(w, h) if p.get("shape") == "roundrect" else 0.0
    c = [_rot(sx * (w / 2 - r), sy * (h / 2 - r), p.get("rot") or 0) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    pts = [(p["x"] + x, p["y"] + y) for x, y in c]
    return list(zip(pts, pts[1:] + pts[:1])), r


def items(geo: dict) -> list[dict]:
    """Copper items: {net, layers, segs, r, box, what}."""
    cu = geo.get("layers") or ["F.Cu", "B.Cu"]
    out = []

    def add(net, layers, segs, r, what):
        xs = [x for s in segs for x in (s[0][0], s[1][0])]
        ys = [y for s in segs for y in (s[0][1], s[1][1])]
        out.append({"net": net, "layers": set(layers), "segs": segs, "r": r, "what": what,
                    "box": (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)})
    for p in geo.get("pads") or []:
        if not p.get("w"):
            continue
        layers = cu if p.get("kind") == "thru_hole" else [l for l, on in (("F.Cu", p.get("front")), ("B.Cu", p.get("back"))) if on]
        if p.get("kind") == "np_thru_hole":
            layers = cu                      # a bare hole: the screw in it counts as accessible metal
        segs, r = _pad_shape(p)
        add(p.get("net") or "", layers, segs, r, f"{p['ref']}.{p['name']}")
    for t in geo.get("tracks") or []:
        add(t["net"], [t["layer"]], [(tuple(t["a"]), tuple(t["b"]))], t["w"] / 2, f"track on {t['layer']}")
    for a in geo.get("arcs") or []:
        add(a["net"], [a["layer"]], [(tuple(a["a"]), tuple(a["m"])), (tuple(a["m"]), tuple(a["b"]))], a["w"] / 2,
            f"track on {a['layer']}")
    for v in geo.get("vias") or []:
        add(v["net"], cu, [(tuple(v["p"]), tuple(v["p"]))], v["d"] / 2, "via")
    for z in geo.get("zones") or []:
        pts = [tuple(p) for p in z["pts"]]
        if len(pts) > 2:
            add(z["net"], [z["layer"]], list(zip(pts, pts[1:] + pts[:1])), 0.0, f"{z['net']} pour on {z['layer']}")
    return out


def _seg_pt(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    q = (a[0] + t * dx, a[1] + t * dy)
    return math.dist(p, q), q


def _cross(a, b, c, d) -> bool:
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0


def _seg_seg(a, b, c, d):
    """Distance between two segments and the closest points."""
    if _cross(a, b, c, d):
        return 0.0, a, c
    best = None
    for p, (s, e), first in ((a, (c, d), True), (b, (c, d), True), (c, (a, b), False), (d, (a, b), False)):
        dist, q = _seg_pt(p, s, e)
        if best is None or dist < best[0]:
            best = (dist, p, q) if first else (dist, q, p)
    return best


def _gap(i: dict, j: dict):
    best = None
    # only edges near the other item matter (zones have thousands)
    for s in i["segs"]:
        for t in j["segs"]:
            d, p, q = _seg_seg(s[0], s[1], t[0], t[1])
            if best is None or d < best[0]:
                best = (d, p, q)
    return best[0] - i["r"] - j["r"], best[1], best[2]


def _near(box, other, d) -> bool:
    return not (box[2] + d < other[0] or other[2] + d < box[0] or box[3] + d < other[1] or other[3] + d < box[1])


def _clip(item: dict, box, d) -> dict:
    """A zone cut down to the edges near box."""
    if len(item["segs"]) < 8:
        return item
    keep = [s for s in item["segs"] if _near((min(s[0][0], s[1][0]), min(s[0][1], s[1][1]),
                                              max(s[0][0], s[1][0]), max(s[0][1], s[1][1])), box, d)]
    return {**item, "segs": keep} if keep else None


def cutouts(geo: dict) -> list[list[tuple]]:
    """Holes and slots milled through the board: Edge.Cuts loops other than the outline, as polygons."""
    segs = []
    for o in geo.get("outline") or []:
        if o["kind"] == "line":
            segs.append([tuple(o["start"]), tuple(o["end"])])
        elif o["kind"] == "arc":
            segs.append([tuple(o["start"]), tuple(o["mid"]), tuple(o["end"])])
        elif o["kind"] == "circle":
            (cx, cy), (ex, ey) = o["center"], o["end"]
            r = math.dist((cx, cy), (ex, ey))
            segs.append([(cx + r * math.cos(k * math.pi / 6), cy + r * math.sin(k * math.pi / 6)) for k in range(13)])
        elif o["kind"] == "rect":
            (x0, y0), (x1, y1) = o["start"], o["end"]
            segs.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])
    key = lambda p: (round(p[0], 2), round(p[1], 2))  # noqa: E731
    loops, used = [], [False] * len(segs)
    for i, s in enumerate(segs):
        if used[i]:
            continue
        used[i] = True
        loop = list(s)
        grown = True
        while grown and key(loop[0]) != key(loop[-1]):
            grown = False
            for j, t in enumerate(segs):
                if used[j]:
                    continue
                if key(t[0]) == key(loop[-1]):
                    loop += t[1:]
                elif key(t[-1]) == key(loop[-1]):
                    loop += t[::-1][1:]
                else:
                    continue
                used[j] = grown = True
                break
        loops.append(loop)
    if len(loops) < 2:
        return []
    area = lambda L: (max(p[0] for p in L) - min(p[0] for p in L)) * (max(p[1] for p in L) - min(p[1] for p in L))  # noqa: E731
    loops.sort(key=area)
    return loops[:-1]


def _blocked(p, q, holes) -> bool:
    for h in holes:
        edges = list(zip(h, h[1:] + h[:1]))
        if any(_cross(p, q, a, b) for a, b in edges):
            return True
    return False


def creepage(p, q, holes) -> float:
    """The shortest path from p to q along the board surface, around the slots."""
    if not holes or not _blocked(p, q, holes):
        return math.dist(p, q)
    # around the slots' corners, pushed out a hair so the path does not graze them
    nodes = [p, q]
    for h in holes:
        cx, cy = sum(x for x, _ in h) / len(h), sum(y for _, y in h) / len(h)
        for x, y in h:
            d = math.dist((x, y), (cx, cy)) or 1
            nodes.append((x + (x - cx) / d * 0.01, y + (y - cy) / d * 0.01))
    dist, q_ = {0: 0.0}, [(0.0, 0)]
    while q_:
        d, u = heapq.heappop(q_)
        if u == 1:
            return d
        if d > dist.get(u, 1e18):
            continue
        for v in range(len(nodes)):
            if v != u and not _blocked(nodes[u], nodes[v], holes):
                nd = d + math.dist(nodes[u], nodes[v])
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    heapq.heappush(q_, (nd, v))
    return math.inf


def check(spec: dict, geo: dict) -> dict:
    """After routing: the closest approach between the mains side and everything else, against the requirements."""
    cfg = config(spec)
    if not cfg:
        return {"mains": False, "problems": [], "warnings": []}
    req = requirements(cfg)
    hot, crossing = sides(spec, cfg)
    mains_nets = set(cfg["nets"])
    holes = cutouts(geo)
    its = items(geo)
    problems, worst = [], {}
    outer = {"F.Cu", "B.Cu"}
    reach = max(req["creepage_mm"], req["functional_outer_mm"]) + 0.5
    hot_items = [i for i in its if i["net"] in hot]
    for i in hot_items:
        for j in its:
            if j is i or j["net"] == i["net"] or (j["net"] in hot and not (i["net"] in mains_nets and j["net"] in mains_nets)):
                continue
            layers = i["layers"] & j["layers"]
            if not layers or not _near(i["box"], j["box"], reach):
                continue
            j2 = _clip(j, i["box"], reach)
            if not j2:
                continue
            gap, p, q = _gap(i, j2)
            same_part = "." in i["what"] and i["what"].split(".")[0] == j["what"].split(".")[0]
            if j["net"] in hot:
                if same_part:
                    continue                    # a part's own pin spacing is the maker's rating
                kind = "functional"
                need = req["functional_outer_mm"] if layers & outer else req["functional_inner_mm"]
                creep_need = None
            elif cfg["isolated"]:
                kind = "isolation"
                need, creep_need = req["clearance_mm"], (req["creepage_mm"] if layers & outer else None)
            else:
                continue
            creep = None
            if creep_need is not None:
                creep = max(gap, 0)
                if holes and _blocked(p, q, holes):
                    # around the slot: from the closest points or any corner of either item
                    pi = [p] + [s[0] for s in i["segs"]][:8]
                    pj = [q] + [s[0] for s in j2["segs"]][:8]
                    creep = max(creep, min(creepage(a, b, holes) for a in pi for b in pj) - i["r"] - j["r"])
            name = lambda x: x["what"] if x["what"].count(".") == 1 else x["net"]  # noqa: E731
            key = (kind, *sorted((name(i), name(j)))) if kind == "functional" else (kind, name(i), name(j))
            row = {"kind": kind, "a": f"{i['what']} ({i['net']})", "b": f"{j['what']} ({j['net'] or 'no net'})",
                   "clearance_mm": round(max(gap, 0), 2), "need_mm": need,
                   "creepage_mm": round(creep, 2) if creep is not None and creep != math.inf else None,
                   "need_creepage_mm": creep_need, "at": [round(p[0], 2), round(p[1], 2)]}
            # KiCad draws arcs as chords to 5 µm; allow for that
            row["ok"] = gap >= need - TOL and (creep_need is None or (creep or 0) >= creep_need - TOL)
            score = min(gap / need if need else 9, (creep or 9) / creep_need if creep_need else 9)
            if key not in worst or score < worst[key][0]:
                worst[key] = (score, row)
    rows = sorted((r for _, r in worst.values()), key=lambda r: (r["ok"], r["clearance_mm"]))
    for r in rows:
        if r["ok"]:
            continue
        if r["kind"] == "functional":
            problems.append(f"{r['a']} is {r['clearance_mm']:g} mm from {r['b']}; mains needs {r['need_mm']:g} mm "
                            f"(IPC-2221, {req['peak_v']} V peak)")
        else:
            what = [f"{r['clearance_mm']:g} mm clearance (needs {r['need_mm']:g})"]
            if r["need_creepage_mm"] is not None:
                what.append(f"{r['creepage_mm']} mm creepage (needs {r['need_creepage_mm']:g})")
            problems.append(f"Isolation: {r['a']} to {r['b']}: " + ", ".join(what) + " for reinforced insulation; move "
                            "the parts apart or cut a slot between them")
    return {"mains": True, "config": cfg, "requirements": req, "mains_side": sorted(hot),
            "barrier": [{"ref": r, "nets": n} for r, n in crossing.items()], "slots": len(holes),
            "rows": rows[:40], "problems": problems[:30], "warnings": [],
            "note": "Pollution degree 2, FR-4 (material group IIIb), overvoltage category "
                    f"{cfg['overvoltage_category']}; an aid, not a certification."}


# ---------------------------------------------------------------- slots under barrier parts

def slots_for(spec: dict, geo: dict) -> list[dict]:
    """A milled slot under each barrier part whose mains and low-voltage pins are closer than the creepage needed:
    {ref, centre, length, angle, width} in board coordinates."""
    cfg = config(spec)
    if not cfg or not cfg["isolated"]:
        return []
    req = requirements(cfg)
    hot, crossing = sides(spec, cfg)
    comps = {c["ref"]: c for c in spec.get("components") or []}
    out = []
    for ref in crossing:
        if comps.get(ref, {}).get("slot") is False:
            continue
        pads = [p for p in geo.get("pads") or [] if p["ref"] == ref and p.get("net")]
        hp = [p for p in pads if p["net"] in hot]
        cp = [p for p in pads if p["net"] not in hot]
        if not hp or not cp:
            continue
        hi = [dict(i, pad=p) for p in hp for i in items({"pads": [p]})]
        ci = [dict(i, pad=p) for p in cp for i in items({"pads": [p]})]
        gap = min(_gap(a, b)[0] for a in hi for b in ci)
        if gap >= req["creepage_mm"]:
            continue
        # the line between the two groups of pins
        hx, hy = sum(p["x"] for p in hp) / len(hp), sum(p["y"] for p in hp) / len(hp)
        cx, cy = sum(p["x"] for p in cp) / len(cp), sum(p["y"] for p in cp) / len(cp)
        L = math.hypot(cx - hx, cy - hy) or 1
        ux, uy = (cx - hx) / L, (cy - hy) / L
        vx, vy = -uy, ux

        def proj(p, ax, ay):
            ext = max(p["w"], p["h"]) / 2
            t = (p["x"] - hx) * ax + (p["y"] - hy) * ay
            return t - ext, t + ext
        h_edge = max(proj(p, ux, uy)[1] for p in hp)
        c_edge = min(proj(p, ux, uy)[0] for p in cp)
        if c_edge - h_edge < SLOT_W + 0.4:
            out.append({"ref": ref, "fits": False, "gap_mm": round(gap, 2)})
            continue
        mid = (h_edge + c_edge) / 2
        span = [v for p in pads for v in proj(p, vx, vy)]
        half_gap = (c_edge - h_edge) / 2
        ext = math.sqrt(max((req["creepage_mm"] / 2) ** 2 - half_gap ** 2, 0)) + 0.5
        lo, hi_ = min(span) - ext, max(span) + ext
        centre = (hx + ux * mid + vx * (lo + hi_) / 2, hy + uy * mid + vy * (lo + hi_) / 2)
        out.append({"ref": ref, "fits": True, "centre": [round(centre[0], 3), round(centre[1], 3)],
                    "length": round(hi_ - lo, 2), "width": SLOT_W,
                    "angle": round(math.degrees(math.atan2(vy, vx)), 2), "gap_mm": round(gap, 2)})
    return out


def slot_outline(s: dict) -> list[list]:
    """Edge.Cuts lines and arcs for a slot with rounded ends."""
    from .kicad.board import _stroke, uid
    from .sexpr import S, Sym
    a = math.radians(s["angle"])
    ux, uy = math.cos(a), math.sin(a)
    vx, vy = -uy, ux
    half, r = s["length"] / 2 - s["width"] / 2, s["width"] / 2
    cx, cy = s["centre"]

    def pt(t, n):
        return round(cx + ux * t + vx * n, 4), round(cy + uy * t + vy * n, 4)

    def node(kind, *pts):
        keys = ("start", "mid", "end") if kind == "gr_arc" else ("start", "end")
        return [Sym(kind), *[S(Sym(k), *p) for k, p in zip(keys, pts)], _stroke(), S(Sym("layer"), "Edge.Cuts"),
                [Sym("uuid"), uid()]]
    return [node("gr_line", pt(-half, r), pt(half, r)),
            node("gr_arc", pt(half, r), pt(half + r, 0), pt(half, -r)),
            node("gr_line", pt(half, -r), pt(-half, -r)),
            node("gr_arc", pt(-half, -r), pt(-half - r, 0), pt(-half, r))]


def add_slots(pcb, spec: dict, geo: dict) -> list[dict]:
    """Cut the slots into a placed board, before routing."""
    from . import sexpr
    slots = slots_for(spec, geo)
    cut = [s for s in slots if s.get("fits")]
    if cut:
        tree = sexpr.parse(pcb.read_text(encoding="utf-8"))
        for s in cut:
            tree += slot_outline(s)
        pcb.write_text(sexpr.dumps(tree), encoding="utf-8")
    return slots


# pad to pad is left out: a part's own pins are rated by its maker, and check() measures pads of different parts
FUNC = "A.NetClass == 'Mains' && B.NetClass == 'Mains' && !(A.Type == 'Pad' && B.Type == 'Pad')"


def dru_rules(spec: dict) -> list[str]:
    """Custom rules for KiCad's DRC (and TraceMaker): functional spacing on the mains nets, reinforced clearance
    and creepage from the mains side to everything else."""
    cfg = config(spec)
    if not cfg:
        return []
    req = requirements(cfg)
    lines = [f"# Mains: {cfg['volts']:g} V working, see wholeboard/mains.py",
             '(rule "Mains: functional spacing, outer layers"', '  (layer outer)',
             f"  (condition \"{FUNC}\")",
             f"  (constraint clearance (min {req['functional_outer_mm']:g}mm)))",
             '(rule "Mains: functional spacing, inner layers"', '  (layer inner)',
             f"  (condition \"{FUNC}\")",
             f"  (constraint clearance (min {req['functional_inner_mm']:g}mm)))"]
    if cfg["isolated"]:
        iso = ("(A.NetClass == 'Mains' || A.NetClass == 'Mains side') && "
               "B.NetClass != 'Mains' && B.NetClass != 'Mains side'")
        lines += ['(rule "Mains: reinforced isolation"', f'  (condition "{iso}")',
                  f"  (constraint clearance (min {req['clearance_mm']:g}mm))",
                  f"  (constraint creepage (min {req['creepage_mm']:g}mm)))",
                  # tracks, vias and pours on the surface are held to the creepage distance outright (the zone
                  # filler knows no creepage); between pads the creepage rule above counts the slots
                  '(rule "Mains: reinforced isolation, surface copper"', "  (layer outer)",
                  f"  (condition \"{iso} && !(A.Type == 'Pad' && B.Type == 'Pad')\")",
                  f"  (constraint clearance (min {req['creepage_mm']:g}mm)))"]
    return lines


def netclasses(spec: dict) -> dict[str, list[str]]:
    """Mains net class names to their nets, for the project file."""
    cfg = config(spec)
    if not cfg:
        return {}
    hot, _ = sides(spec, cfg)
    return {"Mains": list(cfg["nets"]), "Mains side": sorted(hot - set(cfg["nets"]))}


DOC = re.sub(r"\s+", " ", __doc__.split("\n\n", 1)[1]).strip()
