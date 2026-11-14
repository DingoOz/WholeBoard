"""Antenna placement: what is around each antenna once the board is placed and boxed.

There is no single official rule for antenna keep-outs, so each antenna gets the numbers its vendor publishes (with
the document and section they came from), and a conservative generic figure, labelled as such, where the vendor
gives none. Plastic and metal get separate clearances: plastic close to an antenna lowers its resonant frequency,
metal detunes it and blocks the signal.

On the board: the antenna end at (or past) a board edge, no copper under the antenna, no tall or metal parts in its
keep-out or in front of it, and ground plane under the rest of the module. In the enclosure: the distance from the
antenna to each wall, the lid and the floor, and to metal fittings (screws, heat-set inserts); a metal case needs a
plastic window or an external antenna. Rules pick the starting layout; a VNA measurement in the real enclosure is
the final word (see the bring-up guide).
"""

from __future__ import annotations

import math
import re

from . import design

BAND_24 = (2400.0, 2483.5)          # MHz, the 2.4 GHz ISM band
GENERIC = {"plastic_mm": 10.0, "metal_mm": 25.0, "board_mm": 5.0}
GENERIC_NOTE = "generic, not from the datasheet"
TALL_MM = 3.0                        # parts at least this tall count as obstacles near an antenna
METAL = re.compile(r"conn|usb|rj45|jack|header|pin_?header|socket|battery|bat|cr20|coin|18650|cp_elec|cp_radial|"
                   r"shield|display|oled|lcd|inductor|l_\d|relay|buzzer|crystal|hc-?49|to-220|to-247|heatsink|"
                   r"switch|sw_|button|module", re.I)

ESPRESSIF_DOC = ("Espressif hardware design guidelines (ESP32, ESP32-S3, ESP32-C3, ESP32-C6), PCB Layout Design: "
                 "\"General Principles of PCB Layout for Modules (Positioning a Module on a Base Board)\"")
RAYTAC_DOC = "Raytac MDBT50Q-1MV2 / P1MV2 specification, Version K, section 2.3 \"RF Layout Suggestion (aka Keep-Out Area)\""

# Antennas WholeBoard knows, matched on the footprint. "board" is the copper keep-out drawn in the footprint (KiCad
# draws the vendor's figure); "housing" the clearance to the enclosure. Each figure carries its source.
PROFILES: list[dict] = [
    {"match": r"^RF_Module:ESP32", "vendor": "Espressif", "cite": "Espressif's design guidelines", "kind": "module", "band": BAND_24, "tunable": False,
     "plastic": (15.0, f"{ESPRESSIF_DOC}: \"A clearance of at least 15 mm is recommended in all directions\" "
                       "inside the housing"),
     "metal": (GENERIC["metal_mm"], GENERIC_NOTE + " (Espressif gives 15 mm for the housing, not metal)"),
     "edge": (f"{ESPRESSIF_DOC}: place the module's PCB antenna outside the base board; if it cannot overhang, "
              "put the feed point as close to the board edge as possible, and do not put the module in the "
              "middle of the board with the copper cut away around it"),
     "board_source": "the 15 mm keep-out drawn in KiCad's footprint, from the module datasheet's layout figure"},
    {"match": r"^RF_Module:Raytac_MDBT", "vendor": "Raytac", "cite": "Raytac's specification §2.3", "kind": "module", "band": BAND_24, "tunable": False,
     "plastic": (GENERIC["plastic_mm"], GENERIC_NOTE + " (Raytac publishes no housing distance)"),
     "metal": (GENERIC["metal_mm"], GENERIC_NOTE + " (Raytac publishes no metal distance)"),
     "edge": f"{RAYTAC_DOC}: place the module towards the edge of the PCB; no ground under the antenna in every layer",
     "board_source": "the keep-out drawn in KiCad's footprint (under the antenna, every layer)"},
    {"match": r"^RF_Antenna:", "vendor": None, "kind": "chip", "band": None, "tunable": True,
     "plastic": (GENERIC["plastic_mm"], GENERIC_NOTE), "metal": (GENERIC["metal_mm"], GENERIC_NOTE),
     "edge": "chip antennas need a board edge and a copper-free area around them; follow the antenna maker's "
             "evaluation-board layout", "board_source": GENERIC_NOTE},
    {"match": r"^Connector_Coaxial:(U\.FL|IPEX|MHF|SMA|RP-SMA)", "vendor": None, "kind": "connector",
     "band": None, "tunable": True, "plastic": None, "metal": None, "edge": None, "board_source": None},
]


def profile(footprint: str, comp: dict | None = None) -> dict | None:
    for p in PROFILES:
        if re.search(p["match"], footprint or "", re.I):
            return p
    if comp and comp.get("antenna"):
        return {"match": "", "vendor": None, "kind": "module", "band": BAND_24, "tunable": False,
                "plastic": (GENERIC["plastic_mm"], GENERIC_NOTE), "metal": (GENERIC["metal_mm"], GENERIC_NOTE),
                "edge": "put the antenna end at a board edge", "board_source": GENERIC_NOTE}
    return None


def band(footprint: str, prof: dict) -> tuple[float, float]:
    if prof.get("band"):
        return prof["band"]
    m = re.search(r"(\d{3,4})-(\d{3,4})\s*mhz", footprint, re.I)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(\d{3,4})\s*mhz", footprint, re.I)
    if m:
        f = float(m.group(1))
        return {868.0: (863.0, 870.0), 915.0: (902.0, 928.0), 433.0: (433.05, 434.79)}.get(f, (f * 0.98, f * 1.02))
    if re.search(r"gps|gnss", footprint, re.I):
        return 1559.0, 1610.0
    return BAND_24


# ----------------------------------------------------------------------------------- geometry
Rect = tuple  # (x0, y0, x1, y1)


def _bbox(pts) -> Rect:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _isect(a: Rect, b: Rect) -> Rect | None:
    r = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return r if r[2] > r[0] + 1e-6 and r[3] > r[1] + 1e-6 else None


def _area(r: Rect | None) -> float:
    return (r[2] - r[0]) * (r[3] - r[1]) if r else 0.0


def rect_gap(a: Rect, b: Rect) -> float:
    """Distance between two rectangles (0 when they touch or overlap)."""
    dx = max(b[0] - a[2], a[0] - b[2], 0.0)
    dy = max(b[1] - a[3], a[1] - b[3], 0.0)
    return math.hypot(dx, dy)


def local_shape(lib_id: str) -> dict | None:
    """The antenna area, the copper keep-out and the facing direction, in footprint coordinates.

    The antenna area is where the footprint's keep-out zone overlaps the part's body (its fab outline); the keep-out
    is the zone itself. Without a keep-out zone the whole body counts as the antenna (a chip antenna, say)."""
    from .kicad import libs
    from .sexpr import find, find_all, num, tag, walk
    fp = libs.load_footprint(lib_id)
    if not fp:
        return None
    fab = []
    for g in fp.node:
        if tag(g) in ("fp_line", "fp_rect", "fp_poly", "fp_arc", "fp_circle"):
            ly = find(g, "layer")
            if ly and str(ly[1]) == "F.Fab":
                fab += [(num(p[1]), num(p[2])) for p in walk(g) if tag(p) in ("start", "end", "xy", "mid")]
    body = _bbox(fab) if fab else tuple(fp.courtyard)
    zones = []
    for z in find_all(fp.node, "zone"):
        if not find(z, "keepout"):
            continue
        pts = find(find(z, "polygon") or [], "pts")
        xy = [(num(p[1]), num(p[2])) for p in (pts or [])[1:] if tag(p) == "xy"]
        if len(xy) >= 3:
            zones.append(_bbox(xy))
    keep = max(zones, key=lambda z: _area(_isect(z, body)), default=None)
    ant = _isect(keep, body) if keep else None
    if not ant:
        return {"body": body, "antenna": body, "keepout": None, "facing": None}
    bc = ((body[0] + body[2]) / 2, (body[1] + body[3]) / 2)
    ac = ((ant[0] + ant[2]) / 2, (ant[1] + ant[3]) / 2)
    dx, dy = ac[0] - bc[0], ac[1] - bc[1]
    facing = ((1.0 if dx > 0 else -1.0), 0.0) if abs(dx) > abs(dy) else (0.0, (1.0 if dy > 0 else -1.0))
    return {"body": body, "antenna": ant, "keepout": keep, "facing": facing}


def _to_board(fx: float, fy: float, rot: float, bottom: bool):
    a = math.radians(rot)
    c, s = math.cos(a), math.sin(a)

    def pt(x: float, y: float) -> tuple[float, float]:
        if bottom:
            y = -y
        return fx + x * c + y * s, fy - x * s + y * c
    return pt


def _rect_to_board(r: Rect | None, pt) -> Rect | None:
    if r is None:
        return None
    return _bbox([pt(r[0], r[1]), pt(r[2], r[1]), pt(r[2], r[3]), pt(r[0], r[3])])


def find_antennas(spec: dict, geo: dict) -> list[dict]:
    """Every antenna on the placed board, in board coordinates."""
    comps = {c["ref"]: c for c in spec.get("components") or []}
    out = []
    for f in geo.get("footprints") or []:
        comp = comps.get(f["ref"], {})
        prof = profile(f["lib"], comp)
        if not prof:
            continue
        shape = local_shape(f["lib"])
        if not shape:
            if not f.get("court"):
                continue
            c = f["court"]
            shape = {"body": None, "antenna": None, "keepout": None, "facing": None, "_board": tuple(c)}
        bottom = f.get("side") == "B.Cu"
        pt = _to_board(f["x"], f["y"], f.get("rot") or 0.0, bottom)
        body = _rect_to_board(shape["body"], pt) if shape["body"] else shape.get("_board")
        ant = _rect_to_board(shape["antenna"], pt) if shape["antenna"] else body
        keep = _rect_to_board(shape["keepout"], pt)
        facing = None
        if shape["facing"]:
            ox, oy = pt(0, 0)
            fx_, fy_ = pt(*shape["facing"])
            facing = (round(fx_ - ox, 6), round(fy_ - oy, 6))
        lo, hi = band(f["lib"], prof)
        out.append({"ref": f["ref"], "footprint": f["lib"], "value": comp.get("value", ""), "kind": prof["kind"],
                    "vendor": prof["vendor"], "cite": prof.get("cite"), "band_mhz": [lo, hi], "tunable": prof["tunable"],
                    "body": body, "antenna": ant, "keepout": keep, "facing": facing, "side": "bottom" if bottom else "top",
                    "plastic_mm": prof["plastic"][0] if prof["plastic"] else None,
                    "metal_mm": prof["metal"][0] if prof["metal"] else None,
                    "sources": {k: v for k, v in (("plastic", prof["plastic"] and prof["plastic"][1]),
                                                  ("metal", prof["metal"] and prof["metal"][1]),
                                                  ("placement", prof["edge"]),
                                                  ("board keep-out", prof["board_source"])) if v}})
    return out


def cite(a: dict, key: str) -> str:
    """A short name for where a figure comes from, for messages (the full source is in a["sources"])."""
    src = (a.get("sources") or {}).get(key) or ""
    return "generic figure, not from the datasheet" if not src or src.startswith("generic") else (a.get("cite") or "vendor")


def _ray_exit(poly: list, x: float, y: float, d: tuple[float, float]) -> float | None:
    """Distance from (x, y) along d to the outline, or None if the ray never meets it."""
    best = None
    for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]):
        ex, ey = bx - ax, by - ay
        den = d[0] * ey - d[1] * ex
        if abs(den) < 1e-12:
            continue
        t = ((ax - x) * ey - (ay - y) * ex) / den
        u = ((ax - x) * d[1] - (ay - y) * d[0]) / den
        if t >= -1e-9 and -1e-9 <= u <= 1 + 1e-9:
            best = t if best is None else min(best, t)
    return best


def _outer_edge(a: dict) -> tuple[float, float]:
    """The middle of the antenna's far edge (in its facing direction)."""
    r, (dx, dy) = a["antenna"], a["facing"]
    cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
    if dx:
        return (r[2] if dx > 0 else r[0]), cy
    return cx, (r[3] if dy > 0 else r[1])


def edge_distance(a: dict, poly: list) -> float | None:
    """How far inside the board edge the antenna's end is; negative when it overhangs."""
    if not a.get("facing"):
        r = a["antenna"]
        return min(design.edge_distance(poly, x, y) for x, y in ((r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3]),
                                                                  ((r[0] + r[2]) / 2, r[1]), ((r[0] + r[2]) / 2, r[3]),
                                                                  (r[0], (r[1] + r[3]) / 2), (r[2], (r[1] + r[3]) / 2)))
    x, y = _outer_edge(a)
    if not design.inside(poly, x, y):
        back = _ray_exit(poly, x, y, (-a["facing"][0], -a["facing"][1]))
        return -back if back is not None else None
    return _ray_exit(poly, x, y, a["facing"])


def _in_front(a: dict, r: Rect) -> bool:
    """Whether rectangle r is ahead of the antenna (in its facing direction) and overlaps its width."""
    if not a.get("facing"):
        return False
    ant, (dx, dy) = a["antenna"], a["facing"]
    if dx:
        ahead = r[0] >= ant[2] - 0.01 if dx > 0 else r[2] <= ant[0] + 0.01
        return ahead and r[1] < ant[3] and r[3] > ant[1]
    ahead = r[1] >= ant[3] - 0.01 if dy > 0 else r[3] <= ant[1] + 0.01
    return ahead and r[0] < ant[2] and r[2] > ant[0]


def _grid(r: Rect, step: float = 0.5):
    nx, ny = max(2, int((r[2] - r[0]) / step) + 1), max(2, int((r[3] - r[1]) / step) + 1)
    for i in range(nx):
        for j in range(ny):
            yield r[0] + (r[2] - r[0]) * i / (nx - 1), r[1] + (r[3] - r[1]) * j / (ny - 1)


def _shrink(r: Rect, d: float) -> Rect:
    return r[0] + d, r[1] + d, r[2] - d, r[3] - d


def _seg_hits_rect(a, b, r: Rect, w: float = 0.0) -> bool:
    r = (r[0] - w / 2, r[1] - w / 2, r[2] + w / 2, r[3] + w / 2)
    for t in (i / 20 for i in range(21)):
        x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
            return True
    return False


def _ground_nets(spec: dict) -> set[str]:
    return {n["name"] for n in spec.get("nets") or [] if re.match(r"^(GND|VSS|0V|AGND|DGND|PGND)\w*$", n["name"], re.I)}


def _part_obstacles(spec: dict, geo: dict, a: dict, heights: dict | None) -> list[dict]:
    from . import enclosure
    tht = {p["ref"] for p in geo.get("pads") or [] if p.get("kind") == "thru_hole"}
    comps = {c["ref"]: c for c in spec.get("components") or []}
    out = []
    for f in geo.get("footprints") or []:
        if f["ref"] == a["ref"] or not f.get("court") or "mountinghole" in f["lib"].lower():
            continue
        h = (heights or {}).get(f["ref"], comps.get(f["ref"], {}).get("height_mm"))
        info = enclosure.classify(f["lib"], f["ref"] in tht, h)
        if info["kind"] == "none":
            continue
        metal = bool(METAL.search(f["lib"])) or info["kind"] in ("port", "header")
        if info["height"] < TALL_MM and not metal:
            continue
        out.append({"ref": f["ref"], "lib": f["lib"], "rect": tuple(f["court"]), "height_mm": info["height"],
                    "metal": metal, "side": "bottom" if f.get("side") == "B.Cu" else "top"})
    return out


def check(spec: dict, geo: dict, heights: dict | None = None) -> dict:
    """The board-level antenna checks. Problems are things a vendor rule forbids; warnings are worth a look."""
    from .simlab import outline_polygon
    spec = design.normalise(spec)
    ants = find_antennas(spec, geo)
    problems, warnings, rows = [], [], []
    if not ants:
        return {"antennas": [], "problems": [], "warnings": [], "ok": True}
    poly = outline_polygon(geo)
    gnd = _ground_nets(spec)
    for a in ants:
        row = {k: a[k] for k in ("ref", "footprint", "kind", "vendor", "band_mhz", "plastic_mm", "metal_mm",
                                 "sources", "side", "tunable")}
        row.update(antenna=_round(a["antenna"]), keepout=_round(a["keepout"]), body=_round(a["body"]),
                   facing=a["facing"], findings=[])
        rows.append(row)

        def note(level: str, text: str, where=None):
            (problems if level == "problem" else warnings).append(text)
            row["findings"].append({"level": level, "text": text, **({"at": _round(where)} if where else {})})

        if a["kind"] == "connector":
            row["edge_mm"] = None
            _feed_match(spec, a, note)
            continue
        ant = a["antenna"]
        # 1. the antenna end at a board edge
        d = edge_distance(a, poly)
        row["edge_mm"] = round(d, 2) if d is not None else None
        if d is None:
            note("warning", f"{a['ref']}: the antenna's end does not face a board edge. Turn the module so its "
                            "antenna end points out of the board.", ant)
        elif d > 1.0:
            note("warning" if a["kind"] == "module" else "problem",
                 f"{a['ref']}: the antenna end is {d:.1f} mm inside the board edge. Move it to the edge, or past "
                 f"it ({cite(a, 'placement')}).", ant)
        # 2. no copper under the antenna (its own pads aside)
        area = _shrink(ant, 0.05)
        hits = set()
        for t in geo.get("tracks") or []:
            if _seg_hits_rect(t["a"], t["b"], area, t.get("w", 0)):
                hits.add(f"a track on {t.get('net') or 'no net'} ({t['layer']})")
        for v in geo.get("vias") or []:
            if area[0] - v["d"] / 2 <= v["p"][0] <= area[2] + v["d"] / 2 and area[1] - v["d"] / 2 <= v["p"][1] <= area[3] + v["d"] / 2:
                hits.add(f"a via on {v.get('net') or 'no net'}")
        for z in geo.get("zones") or []:
            pts = z["pts"]
            if len(pts) >= 3 and any(design.inside(pts, x, y) for x, y in _grid(area, 0.5)):
                hits.add(f"the {z.get('net') or 'copper'} pour on {z['layer']}")
        for p in geo.get("pads") or []:
            if p["ref"] != a["ref"] and area[0] <= p["x"] <= area[2] and area[1] <= p["y"] <= area[3]:
                hits.add(f"pad {p['ref']}.{p['name']}")
        if hits:
            note("problem", f"{a['ref']}: copper under the antenna: {', '.join(sorted(hits)[:5])}. Keep every layer "
                            "clear there (the footprint's keep-out does this when the board is built by WholeBoard).", ant)
        # 3. tall or metal parts in the keep-out, or near and in front of the antenna
        reach = a["metal_mm"] or GENERIC["metal_mm"]
        for o in _part_obstacles(spec, geo, a, heights):
            gap = rect_gap(ant, o["rect"])
            what = f"{o['ref']} ({'metal, ' if o['metal'] else ''}{o['height_mm']:g} mm tall)"
            if a["keepout"] and _isect(a["keepout"], o["rect"]):
                note("problem", f"{a['ref']}: {what} is inside the antenna keep-out ({cite(a, 'board keep-out')}). "
                                "Move it out.", o["rect"])
            elif gap < reach and _in_front(a, o["rect"]):
                note("warning", f"{a['ref']}: {what} stands {gap:.1f} mm in front of the antenna; keep it at least "
                                f"{reach:g} mm away or out of the antenna's path ({cite(a, 'metal')}).",
                     o["rect"])
            elif gap < min(GENERIC["board_mm"], reach) and (o["metal"] or o["height_mm"] >= 2 * TALL_MM):
                note("warning", f"{a['ref']}: {what} is {gap:.1f} mm from the antenna; move it further away "
                                f"(at least {GENERIC['board_mm']:g} mm, {GENERIC_NOTE}).", o["rect"])
        # 4. ground plane under the rest of the module
        if a["kind"] == "module" and a["body"] and a["body"] != ant and gnd:
            rest = _rest_of_body(a)
            pts = list(_grid(rest, 1.0)) if rest else []
            zones = [z["pts"] for z in geo.get("zones") or [] if z.get("net") in gnd and len(z["pts"]) >= 3]
            covered = sum(1 for x, y in pts if any(design.inside(z, x, y) for z in zones))
            row["ground_cover"] = round(covered / len(pts), 2) if pts else None
            if pts and covered / len(pts) < 0.5:
                note("warning", f"{a['ref']}: only {covered / len(pts):.0%} of the board under the module (away from "
                                "the antenna) has ground copper. The antenna needs ground plane on the module's side: "
                                "pour GND under it with plenty of vias.", rest)
        if a["tunable"]:
            _feed_match(spec, a, note)
    return {"antennas": rows, "problems": problems, "warnings": warnings, "ok": not problems,
            "note": "Rules pick the starting layout. The VNA measurement in the real enclosure (bring-up guide) is the "
                    "final word."}


def placement_warnings(spec: dict) -> list[str]:
    """Before layout: each antenna module should be fixed at a board edge with its antenna pointing out."""
    b = spec.get("board") or {}
    w, h = float(b.get("width_mm") or 0), float(b.get("height_mm") or 0)
    poly = b.get("outline") or [[0, 0], [w, 0], [w, h], [0, h]]
    out, fps = [], []
    for c in spec.get("components") or []:
        prof = profile(str(c.get("footprint") or ""), c)
        if not prof or prof["kind"] == "connector":
            continue
        pl = c.get("placement") or {}
        if pl.get("x_mm") is None or pl.get("y_mm") is None:
            if prof["kind"] == "module":
                out.append(f"{c['ref']} has an antenna: fix its placement at a board edge with the antenna end "
                           "pointing out of the board, so the router does not bury it in the middle")
            continue
        fps.append({"ref": c["ref"], "lib": c["footprint"], "x": float(pl["x_mm"]), "y": float(pl["y_mm"]),
                    "rot": float(pl.get("rotation") or 0),
                    "side": "B.Cu" if str(pl.get("side") or "top") == "bottom" else "F.Cu"})
    for a in find_antennas(spec, {"footprints": fps}):
        d = edge_distance(a, poly)
        if d is None:
            out.append(f"{a['ref']}: its antenna points into the board. Turn it so the antenna end faces the "
                       "nearest edge (KiCad's modules have the antenna at the top: rotation 0 for the top edge, 90 "
                       "left, 180 bottom, 270 right)")
        elif d > 1.0:
            out.append(f"{a['ref']}: its antenna end is {d:.1f} mm inside the board edge; move it to the edge or past it")
    return out


def _rest_of_body(a: dict) -> Rect | None:
    b, n = a["body"], a["antenna"]
    if not a.get("facing"):
        return None
    dx, dy = a["facing"]
    if dx > 0:
        r = (b[0], b[1], n[0], b[3])
    elif dx < 0:
        r = (n[2], b[1], b[2], b[3])
    elif dy > 0:
        r = (b[0], b[1], b[2], n[1])
    else:
        r = (b[0], n[3], b[2], b[3])
    return r if _area(r) > 1 else None


def _round(r):
    return [round(v, 2) for v in r] if r else None


# ----------------------------------------------------------------------------------- matching network
ZERO = re.compile(r"^(0|0r|0r0|0ohm|0 ?Ω|0e)$", re.I)


def feed_net(spec: dict, ref: str) -> str | None:
    """The antenna's signal net: its pin that is not ground."""
    gnd = _ground_nets(spec)
    for n in spec.get("nets") or []:
        if n["name"] in gnd:
            continue
        if any(re.match(rf"^{re.escape(ref)}[.:]", p) for p in n["pins"]):
            return n["name"]
    return None


def has_pi_match(spec: dict, net: str | None) -> bool:
    """A pi network on the feed: a 0 Ω series part, with a part to ground on each side of it."""
    if not net:
        return False
    comps = {c["ref"]: c for c in spec.get("components") or []}
    gnd = _ground_nets(spec)
    nets_of: dict[str, set[str]] = {}
    for n in spec.get("nets") or []:
        for p in n["pins"]:
            nets_of.setdefault(re.split(r"[.:]", p)[0], set()).add(n["name"])

    def shunt_on(side: str, series: str) -> bool:
        return any(r != series and side in ns and ns & gnd and r[0] in "CLR" for r, ns in nets_of.items())
    for r, ns in nets_of.items():
        if net not in ns or not r.startswith("R") or len(ns) != 2:
            continue
        if not ZERO.match(str(comps.get(r, {}).get("value", "")).replace(" ", "")):
            continue
        (other,) = ns - {net}
        if other not in gnd and shunt_on(net, r) and shunt_on(other, r):
            return True
    return False


def _feed_match(spec: dict, a: dict, note) -> None:
    net = feed_net(spec, a["ref"])
    if not has_pi_match(spec, net):
        what = "chip antenna" if a["kind"] == "chip" else "antenna connector"
        note("warning", f"{a['ref']}: the {what}'s feed{f' ({net})' if net else ''} has no pi matching network. "
                        "Add the antenna_pi_match block (a 0 Ω series part and two unfitted shunt pads) between the "
                        "radio and the antenna, so it can be re-tuned for the enclosure after the VNA measurement.")


# ----------------------------------------------------------------------------------- enclosure
def box_check(ants: list[dict], cavity: Rect, z: dict, fittings: list[dict], material: str = "PLA",
              module_h: dict[str, float] | None = None) -> dict:
    """Distances from each antenna to the box around it: walls, lid, floor and metal fittings.

    Everything is in board coordinates (the antennas' frame): cavity is the inside of the box [x0, y0, x1, y1];
    z gives the heights above the inside floor of the board's underside ("board"), its thickness ("thick") and the
    inside of the lid ("lid"); fittings are metal parts of the box ({"what", "x", "y", "r"})."""
    problems, warnings, rows = [], [], []
    metal_box = bool(re.search(r"alu|metal|steel|brass|zinc|die.?cast", material or "", re.I))
    for a in ants:
        if a["kind"] == "connector":
            continue
        r = a["antenna"]
        plastic, metal = a["plastic_mm"] or GENERIC["plastic_mm"], a["metal_mm"] or GENERIC["metal_mm"]
        need = metal if metal_box else plastic
        src = cite(a, "metal" if metal_box else "plastic")
        h = (module_h or {}).get(a["ref"], 3.2)
        top = z["board"] + z["thick"]
        dists = {"left wall": r[0] - cavity[0], "right wall": cavity[2] - r[2], "top wall": r[1] - cavity[1],
                 "bottom wall": cavity[3] - r[3]}
        if a["side"] == "top":
            dists["lid"], dists["floor"] = z["lid"] - top - h, top
        else:
            dists["lid"], dists["floor"] = z["lid"] - z["board"], z["board"] - h
        row = {"ref": a["ref"], "need_mm": need, "material": material, "source": src,
               "distances": {k: round(v, 1) for k, v in dists.items()}, "fittings": []}
        rows.append(row)
        facing = _facing_wall(a)
        for k, v in sorted(dists.items(), key=lambda kv: kv[1]):
            if v < need:
                txt = (f"{a['ref']}: the antenna is {max(v, 0):.1f} mm from the {k}" + (" it faces" if k == facing else "")
                       + f"; keep {need:g} mm ({src})")
                (problems if k == facing and v < need / 3 else warnings).append(txt)
        for fit in fittings:
            g = rect_gap(r, (fit["x"] - fit["r"], fit["y"] - fit["r"], fit["x"] + fit["r"], fit["y"] + fit["r"]))
            row["fittings"].append({"what": fit["what"], "gap_mm": round(g, 1)})
            if g < metal:
                warnings.append(f"{a['ref']}: a {fit['what']} is {g:.1f} mm from the antenna; keep metal {metal:g} mm "
                                f"away ({cite(a, 'metal')})")
        if metal_box:
            problems.append(f"{a['ref']}: the {material} case blocks the antenna. Cut a plastic window over it, or use "
                            "an external antenna: a u.FL connector on the board and a u.FL-to-SMA bulkhead through "
                            "the case wall.")
    return {"rows": rows, "problems": problems, "warnings": warnings, "metal_case": metal_box, "material": material}


def job_check(ants: list[dict], job: dict, origin: tuple[float, float], board_h: float, material: str = "PLA",
              heat_inserts: bool = False) -> dict:
    """box_check for a printed enclosure: job is enclosure.resolve()'s, origin the board's top-left corner (the
    enclosure works in board-relative coordinates, the antennas in absolute ones)."""
    wall, b = job["wall"], job["board"]
    ox, oy = origin
    c0x, c1y = wall - b["x"], b["y"] + board_h - wall            # cavity corners, board-relative
    cav = (ox + c0x, oy + c1y - job["cavity"][3], ox + c0x + job["cavity"][2], oy + c1y)

    def back(x: float, y: float) -> tuple[float, float]:        # enclosure X, Y -> absolute board x, y
        return ox + x - wall + c0x, oy + c1y + wall - y
    fittings = []
    for s in job.get("standoffs") or []:
        x, y = back(s["x"], s["y"])
        fittings.append({"what": "board screw" + (" and heat-set insert" if heat_inserts else ""), "x": x, "y": y,
                         "r": s["pilot"] / 2 + 0.5})
    for c in (job.get("lid") or {}).get("columns") or []:
        x, y = back(c["x"], c["y"])
        fittings.append({"what": "lid screw" + (" and heat-set insert" if heat_inserts else ""), "x": x, "y": y,
                         "r": c["clear"] / 2 + 0.5})
    z = {"board": job["z_board"] - job["floor"], "thick": job["thick"], "lid": job["base_h"] - job["floor"]}
    heights = {k["ref"]: k["h"] for k in job.get("blocks") or []}
    return box_check(ants, cav, z, fittings, material, heights)


def _facing_wall(a: dict) -> str | None:
    if not a.get("facing"):
        return None
    dx, dy = a["facing"]
    if abs(dx) > abs(dy):
        return "right wall" if dx > 0 else "left wall"
    return "bottom wall" if dy > 0 else "top wall"


def wall_room(ants: list[dict]) -> dict[str, float]:
    """For the enclosure generator: the clearance each antenna wants between its facing end and the wall
    (in board directions: left, right, top, bottom), as distances from the antenna to the cavity edge."""
    want: dict[str, float] = {}
    for a in ants:
        if a["kind"] == "connector" or not a.get("facing"):
            continue
        w = _facing_wall(a).split()[0]
        want[w] = max(want.get(w, 0.0), a["plastic_mm"] or GENERIC["plastic_mm"])
    return want


DOC = re.sub(r"\s+", " ", __doc__.split("\n\n", 1)[1]).strip()
