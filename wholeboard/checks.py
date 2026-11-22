"""Physical checks on the routed board: trace heating, voltage drop and decoupling placement.

- Trace current: each track's temperature rise at the current its net carries (from the simulation, or the
  supply's rating when there is no simulation). IPC-2152 by default; IPC-2221's older charts on request
  (rules.trace_heating), and both are reported for comparison.
- Voltage drop: the copper resistance of a power net's tracks between its farthest pads. With simulation results,
  railvolts solves each tracked rail as a resistor network instead and gives the voltage at every load pin.
- Decoupling: every IC supply pin should have a small capacitor on the same net close by.
- Converter hot loop: a buck converter's input current switches round the loop from its VIN pin through the input
  capacitor to its GND pin. The loop's area (and a rough inductance) is warned above rules.hot_loop_mm2 (20 mm²):
  that loop's inductance rings at every switching edge, so datasheets put the input capacitor at the pins.
"""

from __future__ import annotations

import math
import re

from . import design

CU_RHO = 1.72e-8          # ohm·m at 20 °C
CU_THICK_MM = {"outer": 0.035, "inner": 0.0175}   # 1 oz outer, 0.5 oz inner (JLCPCB standard)
MIL = 0.0254              # mm


METHODS = ("ipc2152", "ipc2221")
HOT_LOOP_MM2 = 20.0       # default rules.hot_loop_mm2


def _area_2152(current_a: float, rise_c: float) -> float:
    """Cross-section (mil²) that carries current_a with rise_c of heating: a fit to IPC-2152's baseline chart.

    The baseline is a track alone in a 1.6 mm board with no copper plane nearby, so it is on the safe side for
    boards with pours or planes. IPC-2152 measured inner and outer tracks heating about the same."""
    return (117.555 * rise_c ** -0.913 + 1.15) * current_a ** (0.84 * rise_c ** -0.108 + 1.159)


def trace_rise_c(current_a: float, width_mm: float, thick_mm: float, inner: bool, method: str = "ipc2152") -> float:
    """Temperature rise (°C) of a track carrying current_a, by IPC-2152 or IPC-2221."""
    if current_a <= 0 or width_mm <= 0:
        return 0.0
    area_mil2 = (width_mm / MIL) * (thick_mm / MIL)
    if method == "ipc2221":
        k = 0.024 if inner else 0.048
        return (current_a / (k * area_mil2 ** 0.725)) ** (1 / 0.44)
    lo, hi = 1e-3, 1e4            # the fit's area grows as the rise falls: find the rise whose area is this one
    if _area_2152(current_a, hi) > area_mil2:
        return hi
    for _ in range(80):
        mid = math.sqrt(lo * hi)
        lo, hi = (mid, hi) if _area_2152(current_a, mid) > area_mil2 else (lo, mid)
    return hi


def width_for(current_a: float, rise_c: float, thick_mm: float, inner: bool, method: str = "ipc2152") -> float:
    """Track width (mm) that carries current_a with rise_c of heating."""
    if method == "ipc2221":
        k = 0.024 if inner else 0.048
        area_mil2 = (current_a / (k * rise_c ** 0.44)) ** (1 / 0.725)
    else:
        area_mil2 = _area_2152(current_a, rise_c)
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


def _loop_area(pts: list[tuple[float, float]]) -> float:
    """Area (mm²) of the loop through the points in order (the shoelace formula)."""
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))) / 2


def loop_inductance_nh(area_mm2: float, wire_radius_mm: float = 0.1) -> float:
    """Rough inductance (nH) of a loop of this area: a circular loop of thin wire, in free space. A plane right
    under the loop lowers it; it is for comparing layouts, not a figure to design to."""
    r = math.sqrt(max(area_mm2, 0) / math.pi)
    if r <= wire_radius_mm:
        return 0.0
    return max(4e-7 * math.pi * (r / 1000) * (math.log(8 * r / wire_radius_mm) - 2), 0.0) * 1e9


def _hot_loop(part, ground: str | None, pads: list[dict]) -> dict | None:
    """A buck converter's input loop: its VIN pin, the input capacitor that makes the smallest loop, its GND pin.
    None for a part whose input or ground pins can't be found, and for a boost (its inductor runs from the input
    to the switch node, so its hot loop is on the output side)."""
    vi = part.pad_named("VIN", "VI", "IN", "VCC", "PVIN")
    vin = part.pads.get(vi) if vi else None
    if not vin or not ground:
        return None
    nets_of: dict[str, set] = {}
    for p in pads:
        nets_of.setdefault(p["ref"], set()).add(p["net"])
    sw = part.pad_named("SW", "LX", "PH", "SWITCH")
    sw_net = part.pads.get(sw) if sw else None
    if sw_net and any(re.match(r"L\d", r) and {vin, sw_net} <= n for r, n in nets_of.items()):
        return None
    ic_vin = [p for p in pads if p["ref"] == part.ref and p["net"] == vin]
    ic_gnd = [p for p in pads if p["ref"] == part.ref and p["net"] == ground]
    if not ic_vin or not ic_gnd:
        return None
    best = None
    for ref, nets in nets_of.items():
        if not re.match(r"C\d", ref) or not {vin, ground} <= nets:      # not a connector (CN1)
            continue
        c_vin = [p for p in pads if p["ref"] == ref and p["net"] == vin]
        c_gnd = [p for p in pads if p["ref"] == ref and p["net"] == ground]
        for a in ic_vin:
            for b in c_vin:
                for g in c_gnd:
                    for d in ic_gnd:
                        area = _loop_area([(a["x"], a["y"]), (b["x"], b["y"]), (g["x"], g["y"]), (d["x"], d["y"])])
                        if best is None or area < best[1]:
                            best = (ref, area)
    row = {"ref": part.ref, "vin": vin, "ground": ground, "cap": best[0] if best else None}
    if best:
        row.update(area_mm2=round(best[1], 1), loop_nh=round(loop_inductance_nh(best[1]), 1))
    return row


def run(spec: dict, geo: dict, net_current_ma: dict | None = None, supplies: list[dict] | None = None,
        max_rise_c: float = 10.0, sim_res: dict | None = None) -> dict:
    """sim_res: the simulation's results, for the rail voltages at the load pins (railvolts)."""
    from . import railvolts, sim
    spec = design.normalise(spec)
    method = spec["rules"].get("trace_heating") if spec["rules"].get("trace_heating") in METHODS else "ipc2152"
    other = next(m for m in METHODS if m != method)
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

    # ---- rail voltages at the load pins, through the routed copper
    nl = sim.Netlist(spec, symbol_pins=sim._symbol_pins)
    try:
        rails = railvolts.solve(spec, geo, nl, sim_res)
    except Exception as e:  # noqa: BLE001 - the other checks still run
        rails = {"rails": [], "warnings": [], "note": f"The rail voltages at the pins were not worked out: {e}"}
    warnings += rails["warnings"]
    solved = {r["net"] for r in rails["rails"]}

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
        rise = trace_rise_c(i_a, narrow["w"], thick, inner, method)
        r_ohm, length = _graph_drop(tracks, pads_by_net.get(net, []), i_a)
        drop = r_ohm * i_a
        row = {"net": net, "current_ma": round(i_ma, 1), "narrowest_mm": narrow["w"], "layer": narrow["layer"],
               "rise_c": round(rise, 2), f"rise_{other}_c": round(trace_rise_c(i_a, narrow["w"], thick, inner, other), 2),
               "copper_mohm": round(r_ohm * 1000, 1), "drop_mv": round(drop * 1000, 1),
               "length_mm": round(length, 1)}
        poured = net == pour or net in planes
        if i_a > 0 and rise > max_rise_c and not poured:
            need = width_for(i_a, max_rise_c, thick, inner, method)
            msg = (f"{net}: {i_ma:.0f} mA heats its {narrow['w']:g} mm track by about {rise:.0f} °C; "
                   f"make power tracks at least {need:.2f} mm (rules.power_track_mm)")
            (problems if rise > 3 * max_rise_c else warnings).append(msg)
            row["need_mm"] = round(need, 2)
        from .sim import net_volts
        v = net_volts(net)
        if v and drop > 0.02 * v and not poured and net not in solved:     # solved: warned pin by pin
            warnings.append(f"{net}: {drop * 1000:.0f} mV drop along {length:.0f} mm of track at {i_ma:.0f} mA "
                            f"({drop / v:.1%} of {v:g} V); widen it or shorten the path")
        rows.append(row)

    # ---- decoupling capacitors near IC supply pins
    decoupling, hot_loops = [], []
    loop_limit = float(spec["rules"].get("hot_loop_mm2") or HOT_LOOP_MM2)
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
        loop = _hot_loop(part, nl.ground, pads) if part.kind == "converter" else None
        if loop:
            hot_loops.append({**loop, "limit_mm2": loop_limit})
            if loop["cap"] and loop["area_mm2"] > loop_limit:
                warnings.append(f"{c['ref']}'s input loop ({loop['vin']} through {loop['cap']} to {loop['ground']}) "
                                f"encloses about {loop['area_mm2']:.0f} mm² (roughly {loop['loop_nh']:.0f} nH); "
                                f"keep it under {loop_limit:g} mm² by placing {loop['cap']} right at "
                                f"{c['ref']}'s VIN and GND pins (rules.hot_loop_mm2)")
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
    return {"source": source, "method": method, "tracks": rows, "rails": rails["rails"], "rails_note": rails["note"],
            "decoupling": decoupling, "hot_loops": hot_loops,
            "problems": problems, "warnings": warnings, "ok": not problems}
