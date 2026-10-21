"""Physical checks on the routed board: trace heating, voltage drop and decoupling placement.

- Trace current: IPC-2221 gives each track's temperature rise at the current its net carries
  (from the simulation, or the supply's rating when there is no simulation).
- Voltage drop: the copper resistance of a power net's tracks between its farthest pads.
- Decoupling: every IC supply pin should have a small capacitor on the same net close by.
"""

from __future__ import annotations

import math
import re

from . import design

CU_RHO = 1.72e-8          # ohm·m at 20 °C
CU_THICK_MM = {"outer": 0.035, "inner": 0.0175}   # 1 oz outer, 0.5 oz inner (JLCPCB standard)
MIL = 0.0254              # mm


def trace_rise_c(current_a: float, width_mm: float, thick_mm: float, inner: bool) -> float:
    """IPC-2221 temperature rise (°C) of a track carrying current_a."""
    if current_a <= 0 or width_mm <= 0:
        return 0.0
    k = 0.024 if inner else 0.048
    area_mil2 = (width_mm / MIL) * (thick_mm / MIL)
    return (current_a / (k * area_mil2 ** 0.725)) ** (1 / 0.44)


def width_for(current_a: float, rise_c: float, thick_mm: float, inner: bool) -> float:
    """Track width (mm) that carries current_a with rise_c of heating (IPC-2221)."""
    k = 0.024 if inner else 0.048
    area_mil2 = (current_a / (k * rise_c ** 0.44)) ** (1 / 0.725)
    return area_mil2 / (thick_mm / MIL) * MIL


def _seg_len(t: dict) -> float:
    return math.dist(t["a"], t["b"])


def _resistance(length_mm: float, width_mm: float, thick_mm: float) -> float:
    return CU_RHO * (length_mm / 1000) / ((width_mm / 1000) * (thick_mm / 1000))


def _graph_drop(tracks: list[dict], pads: list[dict], current_a: float) -> tuple[float, float]:
    """Worst copper resistance (ohm) between any two pads of a net along its tracks, and its length."""
    nodes: dict[tuple, int] = {}

    def nid(p):
        key = (round(p[0], 2), round(p[1], 2))
        if key not in nodes:
            nodes[key] = len(nodes)
        return nodes[key]
    edges: dict[int, list[tuple[int, float, float]]] = {}
    for t in tracks:
        a, b = nid(t["a"]), nid(t["b"])
        inner = t["layer"].startswith("In")
        r = _resistance(_seg_len(t), t["w"], CU_THICK_MM["inner" if inner else "outer"])
        edges.setdefault(a, []).append((b, r, _seg_len(t)))
        edges.setdefault(b, []).append((a, r, _seg_len(t)))
    keys = list(nodes)
    pad_nodes = []
    for p in pads:
        best = min(keys, key=lambda k: math.dist(k, (p["x"], p["y"])), default=None)
        if best is not None and math.dist(best, (p["x"], p["y"])) <= max(p["w"], p["h"]) + 0.5:
            pad_nodes.append(nodes[best])
    if len(pad_nodes) < 2:
        return 0.0, 0.0
    worst, worst_len = 0.0, 0.0
    import heapq
    for src in pad_nodes[:12]:
        dist = {src: (0.0, 0.0)}
        heap = [(0.0, 0.0, src)]
        while heap:
            r, ln, n = heapq.heappop(heap)
            if r > dist.get(n, (1e9,))[0]:
                continue
            for m, er, el in edges.get(n, []):
                nr = r + er
                if nr < dist.get(m, (1e9,))[0]:
                    dist[m] = (nr, ln + el)
                    heapq.heappush(heap, (nr, ln + el, m))
        for dn in pad_nodes:
            if dn in dist and dist[dn][0] > worst:
                worst, worst_len = dist[dn]
    return worst, worst_len


def run(spec: dict, geo: dict, net_current_ma: dict | None = None, supplies: list[dict] | None = None,
        max_rise_c: float = 10.0) -> dict:
    spec = design.normalise(spec)
    net_class = {n["name"]: n.get("class") for n in spec["nets"]}
    currents = dict(net_current_ma or {})
    source = "simulation" if currents else "estimate"
    if not currents:
        for s in supplies or []:
            if s.get("max_ma"):
                currents[s["net"]] = float(s["max_ma"])
    problems, warnings, rows = [], [], []
    planes = set((spec["board"].get("planes") or {}).values())
    pour = spec["board"].get("ground_pour")

    by_net: dict[str, list[dict]] = {}
    for t in geo.get("tracks", []):
        if t.get("net"):
            by_net.setdefault(t["net"], []).append(t)
    pads_by_net: dict[str, list[dict]] = {}
    for p in geo.get("pads", []):
        if p.get("net"):
            pads_by_net.setdefault(p["net"], []).append(p)

    # ---- trace current and voltage drop
    for net, tracks in sorted(by_net.items()):
        i_ma = currents.get(net)
        if net_class.get(net) != "power" and (i_ma or 0) < 20:
            continue       # signal nets carrying little current need no check
        i_ma = i_ma or 0.0
        i_a = i_ma / 1000
        narrow = min(tracks, key=lambda t: t["w"])
        inner = narrow["layer"].startswith("In")
        thick = CU_THICK_MM["inner" if inner else "outer"]
        rise = trace_rise_c(i_a, narrow["w"], thick, inner)
        r_ohm, length = _graph_drop(tracks, pads_by_net.get(net, []), i_a)
        drop = r_ohm * i_a
        row = {"net": net, "current_ma": round(i_ma, 1), "narrowest_mm": narrow["w"], "layer": narrow["layer"],
               "rise_c": round(rise, 2), "copper_mohm": round(r_ohm * 1000, 1), "drop_mv": round(drop * 1000, 1),
               "length_mm": round(length, 1)}
        poured = net == pour or net in planes
        if i_a > 0 and rise > max_rise_c and not poured:
            need = width_for(i_a, max_rise_c, thick, inner)
            msg = (f"{net}: {i_ma:.0f} mA heats its {narrow['w']:g} mm track by about {rise:.0f} °C; "
                   f"make power tracks at least {need:.2f} mm (rules.power_track_mm)")
            (problems if rise > 3 * max_rise_c else warnings).append(msg)
            row["need_mm"] = round(need, 2)
        from .sim import net_volts
        v = net_volts(net)
        if v and drop > 0.02 * v and not poured:
            warnings.append(f"{net}: {drop * 1000:.0f} mV drop along {length:.0f} mm of track at {i_ma:.0f} mA "
                            f"({drop / v:.1%} of {v:g} V); widen it or shorten the path")
        rows.append(row)

    # ---- decoupling capacitors near IC supply pins
    from . import sim
    nl = sim.Netlist(spec, symbol_pins=sim._symbol_pins)
    decoupling = []
    caps = [c for c in spec["components"] if c["ref"].startswith("C")]
    cap_val = {c["ref"]: sim.parse_value(c.get("value")) for c in caps}
    pads = geo.get("pads", [])
    for c in spec["components"]:
        if not re.match(r"^(U|IC)\d", c["ref"]):
            continue
        part = nl._part(c)
        part.kind = sim.classify(part)
        if part.kind in ("skip", "connector", "unsupported"):
            continue
        supply = nl._supply_pads(part)
        if part.kind in ("ldo", "converter"):
            supply = [p for p in [part.pad_named("VI", "VIN", "IN"), part.pad_named("VO", "VOUT", "OUT")] if p]
        for pad in supply:
            net = part.pads.get(pad)
            pp = next((p for p in pads if p["ref"] == c["ref"] and p["name"] == pad), None)
            if not net or not pp:
                continue
            best = None
            for cp in pads:
                if cp["net"] != net or not cp["ref"].startswith("C"):
                    continue
                v = cap_val.get(cp["ref"])
                if v is not None and v > 22e-6:
                    continue
                d = math.dist((pp["x"], pp["y"]), (cp["x"], cp["y"]))
                if best is None or d < best[1]:
                    best = (cp["ref"], d)
            row = {"ref": c["ref"], "pad": pad, "pin": part.names.get(pad, pad), "net": net,
                   "cap": best[0] if best else None, "distance_mm": round(best[1], 1) if best else None}
            decoupling.append(row)
            limit = 8.0 if part.kind in ("ldo", "converter") else 5.0
            if best is None:
                warnings.append(f"{c['ref']} pin {pad} ({row['pin']}, {net}) has no decoupling capacitor")
            elif best[1] > limit:
                warnings.append(f"{c['ref']} pin {pad} ({row['pin']}): nearest decoupling capacitor {best[0]} is "
                                f"{best[1]:.1f} mm away; keep it within {limit:g} mm (give it a placement next to "
                                f"{c['ref']})")
    rows.sort(key=lambda r: -r["current_ma"])
    return {"source": source, "tracks": rows, "decoupling": decoupling, "problems": problems,
            "warnings": warnings, "ok": not problems}
