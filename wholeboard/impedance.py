"""Controlled-impedance traces: widths and gaps from JLCPCB's layer stack, and a length check after routing.

A design lists its controlled nets in "impedance":
  [{"nets": ["USB_DP", "USB_DN"], "ohms": 90, "kind": "diff", "max_skew_mm": 1.25},
   {"nets": ["RF_ANT"], "ohms": 50, "kind": "single"},
   {"nets": ["QSPI_SCLK", "QSPI_SD0"], "kind": "bus", "max_length_mm": 30, "max_match_mm": 5}]

An entry may name a routing profile ("profile": "usb2_hs") for an interface's limits; its own fields override
the profile's. Limits: max_length_mm (each net), max_skew_mm (within a pair), max_match_mm (between the nets of
a bus), max_vias (each net), max_stub_mm (a branch off the main run, e.g. to an ESD diode), and match with
max_skew_between_mm (pairs that share a "match" name stay within that length of each other). Profiles: {PROFILES}.
Building blocks carry the profile of the interfaces they provide, so the checks apply wherever they are used.

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

# Routing limits of common interfaces (layout guidelines: USB-IF and the chip makers' design guides).
PROFILES = {
    "usb2_hs": {"title": "USB 2.0 high speed (480 Mbit/s)", "kind": "diff", "ohms": 90, "max_skew_mm": 1.25,
                "max_length_mm": 150, "max_vias": 2, "max_stub_mm": 2.5},
    "usb2_fs": {"title": "USB 2.0 full speed (12 Mbit/s)", "kind": "diff", "ohms": 90, "max_skew_mm": 5,
                "max_vias": 4, "max_stub_mm": 10},
    "ethernet_100": {"title": "100BASE-TX pair", "kind": "diff", "ohms": 100, "max_skew_mm": 1.27,
                     "max_length_mm": 100, "max_vias": 2, "max_stub_mm": 2.5},
    "rmii": {"title": "RMII (50 MHz)", "kind": "bus", "max_length_mm": 100, "max_match_mm": 25, "max_vias": 3},
    "qspi_flash": {"title": "QSPI flash", "kind": "bus", "max_length_mm": 30, "max_match_mm": 5, "max_vias": 2},
    "rf_50": {"title": "50 Ω RF feed", "kind": "single", "ohms": 50, "max_length_mm": 25, "max_vias": 0,
              "max_stub_mm": 1},
}
LIMITS = ("max_length_mm", "max_skew_mm", "max_match_mm", "max_vias", "max_stub_mm", "max_skew_between_mm")


def profiled(group: dict) -> dict:
    """A group with its profile's limits filled in (the group's own fields win)."""
    prof = PROFILES.get(str(group.get("profile") or ""))
    if not prof:
        return dict(group)
    return {**{k: v for k, v in prof.items() if k != "title"}, "profile_title": prof["title"], **group}


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
    group = profiled(group)
    st = stackup(layers, thickness)
    h, er = st["h"], st["er"]
    ohms = float(group.get("ohms") or (90 if group.get("kind") == "diff" else 50))
    out = {"nets": list(group.get("nets") or []), "kind": group.get("kind", "single"), "ohms": ohms,
           "stackup": st["name"], "h_mm": round(h, 4), "er": er,
           **{k: group[k] for k in ("profile", "profile_title", "match", *LIMITS) if group.get(k) is not None}}
    if out["kind"] == "bus":            # length and matching only, no impedance target
        out.update(ohms=None, practical=False, width_mm=None)
        return out
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
        if g.get("profile") and g["profile"] not in PROFILES:
            errors.append(f"impedance[{i}]: unknown profile {g['profile']!r}; use one of {', '.join(PROFILES)}")
            continue
        g = profiled(g)
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
    for i, g in enumerate(r for r in rules(spec) if r["kind"] != "bus"):
        w = g["width_mm"] if g["practical"] else None
        out.append({"name": f"Impedance{i + 1}_{g['ohms']:g}R", "width": w, "gap": g.get("gap_mm"), "nets": g["nets"],
                    "diff": g["kind"] == "diff"})
    return out


def has_pairs(spec: dict) -> bool:
    return any(g.get("kind") == "diff" and len(g.get("nets") or []) == 2 and pair_names(*g["nets"])
               for g in map(profiled, (x for x in spec.get("impedance") or [] if isinstance(x, dict))))


def _stub(geo: dict, net: str) -> float:
    """The longest branch off a net's main run: the copper from the run to a pad that is not one of its ends."""
    import heapq
    key = lambda p: (round(p[0], 3), round(p[1], 3))  # noqa: E731
    segs = [t for t in (geo.get("tracks") or []) + (geo.get("arcs") or []) if t["net"] == net]
    ends = {key(p) for t in segs for p in (t["a"], t["b"])}
    adj: dict = {}

    def link(a, b, d):
        adj.setdefault(a, []).append((b, d))
        adj.setdefault(b, []).append((a, d))
    for t in segs:
        a, b = key(t["a"]), key(t["b"])
        if "m" in t:
            link(a, b, _arc_len(t["a"], t["m"], t["b"]))
            continue
        # a track that another one tees into part-way along is split there
        L = math.dist(a, b)
        cut = sorted((math.dist(a, p), p) for p in ends if p not in (a, b) and L > 0
                     and abs(math.dist(a, p) + math.dist(p, b) - L) < 0.01)
        pts = [a, *(p for _, p in cut), b]
        for p, q in zip(pts, pts[1:]):
            link(p, q, math.dist(p, q))
    if len(adj) < 3:
        return 0.0

    def far(src):
        dist, prev, q = {src: 0.0}, {src: None}, [(0.0, src)]
        while q:
            d, u = heapq.heappop(q)
            if d > dist[u]:
                continue
            for v, w in adj[u]:
                if d + w < dist.get(v, 1e18):
                    dist[v], prev[v] = d + w, u
                    heapq.heappush(q, (d + w, v))
        return max(dist, key=dist.get), dist, prev
    leaves = [n for n, e in adj.items() if len(e) == 1] or list(adj)
    a, _, _ = far(leaves[0])
    b, _, prev = far(a)
    trunk = set()
    n = b
    while n is not None:
        trunk.add(n)
        n = prev[n]
    # distance of every other node from the trunk
    dist, q = {n: 0.0 for n in trunk}, [(0.0, n) for n in trunk]
    while q:
        d, u = heapq.heappop(q)
        if d > dist[u]:
            continue
        for v, w in adj[u]:
            if v not in trunk and d + w < dist.get(v, 1e18):
                dist[v] = d + w
                heapq.heappush(q, (d + w, v))
    return max(dist.values(), default=0.0)


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
                         "width_mm": round(min(widths), 3) if widths else None, "stub_mm": round(_stub(geo, n), 2)})
        row = {**g, "members": rows}
        what = f" ({g['profile_title']})" if g.get("profile_title") else ""
        for m in rows:
            if g.get("max_length_mm") is not None and m["length_mm"] > g["max_length_mm"]:
                warnings.append(f"{m['net']} is {m['length_mm']:g} mm long; keep it under {g['max_length_mm']:g} mm{what}")
            if g.get("max_vias") is not None and m["vias"] > g["max_vias"]:
                warnings.append(f"{m['net']} has {m['vias']} vias; at most {g['max_vias']}{what}")
            if g.get("max_stub_mm") is not None and m["stub_mm"] > g["max_stub_mm"]:
                warnings.append(f"{m['net']} has a {m['stub_mm']:g} mm branch off its main run; keep stubs under "
                                f"{g['max_stub_mm']:g} mm{what} (route through the pad instead of teeing off)")
        lens = [m["length_mm"] for m in rows if m["length_mm"]]
        if g["kind"] == "bus" and len(lens) > 1:
            row["match_mm"] = round(max(lens) - min(lens), 2)
            if g.get("max_match_mm") is not None and row["match_mm"] > g["max_match_mm"]:
                warnings.append(f"{', '.join(g['nets'])}: lengths differ by {row['match_mm']:g} mm; keep them within "
                                f"{g['max_match_mm']:g} mm of each other{what}")
        if g["kind"] == "diff" and len(rows) == 2 and rows[0]["length_mm"] and rows[1]["length_mm"]:
            row["skew_mm"] = round(abs(rows[0]["length_mm"] - rows[1]["length_mm"]), 2)
            if row["skew_mm"] > g["max_skew_mm"]:
                warnings.append(f"{rows[0]['net']}/{rows[1]['net']} differ in length by {row['skew_mm']} mm "
                                f"(limit {g['max_skew_mm']} mm); add a small meander to the shorter one in KiCad")
            if rows[0]["vias"] != rows[1]["vias"]:
                warnings.append(f"{rows[0]['net']} has {rows[0]['vias']} vias but {rows[1]['net']} has {rows[1]['vias']}")
        if g["kind"] == "bus":
            pass
        elif g["practical"]:
            for m in rows:
                if m["width_mm"] and abs(m["width_mm"] - g["width_mm"]) > 0.02:
                    warnings.append(f"{m['net']} is routed {m['width_mm']} mm wide in places; {g['ohms']:g} Ω needs "
                                    f"{g['width_mm']} mm")
        elif g.get("note"):
            warnings.append(g["note"])
        groups.append(row)
    # pairs that must match each other (e.g. the lanes of one link)
    by_match: dict[str, list[dict]] = {}
    for row in groups:
        if row.get("match") and row["kind"] == "diff":
            by_match.setdefault(row["match"], []).append(row)
    for name, rows_ in by_match.items():
        avg = [sum(m["length_mm"] for m in r["members"]) / max(1, len(r["members"])) for r in rows_]
        limit = min((r.get("max_skew_between_mm") for r in rows_ if r.get("max_skew_between_mm") is not None), default=None)
        if len(avg) > 1 and limit is not None and max(avg) - min(avg) > limit:
            warnings.append(f"The {name} pairs differ in length by {max(avg) - min(avg):.1f} mm; keep them within {limit:g} mm")
    return {"groups": groups, "problems": problems, "warnings": warnings,
            "order_note": (f"Order with impedance control, stack-up {groups[0]['stackup']}."
                           if groups and int(spec["board"]["layers"]) >= 4 else "")}


DOC = re.sub(r"\s+", " ", __doc__.split("\n\n", 1)[1]).strip().replace(
    "{PROFILES}", "; ".join(f"{k} ({v['title']})" for k, v in PROFILES.items()))
