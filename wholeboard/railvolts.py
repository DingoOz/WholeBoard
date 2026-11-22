"""Rail voltages at the load pins, through the routed copper.

The simulation runs on the schematic, where a rail is one ideal node. After routing, each power net's tracks and vias
become a resistor network: this puts the rail's source (a regulator's output, or the connector or battery that feeds
it) at the simulated rail voltage, draws each load's peak current at its own supply pins, and solves for the voltage
at every pin. It answers "does the 1.2 V core rail still hold at the far pins of the chip?", which the schematic
simulation cannot.

What counts:
- Loads: each IC's peak current from the simulation (sim_res["loads"]), shared between its supply nets as the model
  shares it and between its pins on each net, and each regulator's input current (an LDO's output current; a
  switching regulator's output power over 85 % efficiency, divided by its input voltage).
- Copper: 1 oz outer and 0.5 oz inner tracks (as checks.py), and plated vias 25 µm thick through the board.
- Left out: rails with a pour or plane (the zone fill is not modelled), and the smaller loads (resistors, LEDs).

A pin more than rules.rail_drop_pct (2 %) below the rail is warned.
"""

from __future__ import annotations

import heapq
import math

from . import design

CU_RHO = 1.72e-8
THICK_MM = {"outer": 0.035, "inner": 0.0175}
PLATING_MM = 0.025
DROP_PCT = 2.0


def _key(p, layer: str) -> tuple:
    return (round(p[0], 3), round(p[1], 3), layer)


def _track_r(length_mm: float, w_mm: float, layer: str) -> float:
    t = THICK_MM["inner" if layer.startswith("In") else "outer"]
    return CU_RHO * (length_mm / 1000) / ((w_mm / 1000) * (t / 1000))


def _via_r(drill_mm: float, board_mm: float) -> float:
    r_out = drill_mm / 2
    r_in = max(r_out - PLATING_MM, 0.0)
    area = math.pi * (r_out ** 2 - r_in ** 2)
    return CU_RHO * (board_mm / 1000) / (area / 1e6) if area > 0 else 1e-3


def _through(p: dict) -> bool:
    """A plated through-hole pad, on every layer. Its drill can read as 0 (an oval drill), so the type counts too."""
    return bool(p.get("drill")) or p.get("kind") == "thru_hole" or bool(p.get("front") and p.get("back"))


def _arc_len(t: dict) -> float:
    return math.dist(t["a"], t["m"]) + math.dist(t["m"], t["b"])        # two chords: close enough for resistance


class _Network:
    """A resistor network built edge by edge; nodes are any hashable names."""

    def __init__(self):
        self.adj: dict = {}

    def link(self, a, b, r: float) -> None:
        if a == b:
            return
        g = 1 / max(r, 1e-9)
        self.adj.setdefault(a, {})
        self.adj.setdefault(b, {})
        self.adj[a][b] = self.adj[a].get(b, 0.0) + g
        self.adj[b][a] = self.adj[b].get(a, 0.0) + g

    def drops(self, source, sinks: dict) -> dict:
        """Voltage drop from source (held at 0) to every node it reaches, with sinks {node: amps} drawn out.
        Exact: Gaussian elimination, lowest degree first, which is cheap on the tree-like copper of a rail."""
        reach, todo = {source}, [source]
        while todo:
            n = todo.pop()
            for m in self.adj.get(n, {}):
                if m not in reach:
                    reach.add(m)
                    todo.append(m)
        free = reach - {source}
        diag = {n: sum(self.adj[n].values()) for n in free}
        off = {n: {m: -g for m, g in self.adj[n].items() if m in free} for n in free}
        b = {n: float(sinks.get(n, 0.0)) for n in free}
        heap = [(len(off[n]), id(n), n) for n in free]
        heapq.heapify(heap)
        order, done = [], set()
        while heap:
            deg, _, k = heapq.heappop(heap)
            if k in done or deg != len(off[k]):
                continue
            done.add(k)
            row = dict(off[k])
            order.append((k, diag[k], row, b[k]))
            for i, aik in row.items():
                del off[i][k]
                f = aik / diag[k]
                diag[i] -= f * aik
                b[i] -= f * b[k]
                for j, akj in row.items():
                    if j != i:
                        off[i][j] = off[i].get(j, 0.0) - f * akj
                heapq.heappush(heap, (len(off[i]), id(i), i))
        d = {source: 0.0}
        for k, akk, row, bk in reversed(order):
            d[k] = (bk - sum(a * d[j] for j, a in row.items())) / akk
        return d


def solve(spec: dict, geo: dict, nl, sim_res: dict | None) -> dict:
    """Voltage at each load pin of each tracked power rail. nl: a sim.Netlist of spec."""
    from . import sim
    spec = design.normalise(spec)
    out = {"rails": [], "warnings": [], "note": ""}
    sim_res = sim_res or {}
    if not sim_res.get("loads") and not sim_res.get("parts"):
        out["note"] = "No simulation results, so the rail voltages at the pins were not worked out."
        return out
    limit = float(spec["rules"].get("rail_drop_pct") or DROP_PCT)
    board_mm = float(spec["board"].get("thickness_mm") or 1.6)
    poured = {spec["board"].get("ground_pour")} | set((spec["board"].get("planes") or {}).values())
    poured |= {z.get("net") for z in geo.get("zones") or []}
    rail_v = {r["net"]: r["volts"] for r in sim_res.get("rails") or []}
    reg_rows = {r["ref"]: r for r in sim_res.get("parts") or [] if r.get("kind") in ("ldo", "converter")}
    pads = geo.get("pads") or []
    pads_of: dict[tuple, list[dict]] = {}
    for p in pads:
        pads_of.setdefault((p["ref"], p["net"]), []).append(p)
    nets_of: dict[str, set] = {}
    for p in pads:
        nets_of.setdefault(p["ref"], set()).add(p["net"])
    parts = {}
    for c in spec["components"]:
        part = nl._part(c)
        part.kind = sim.classify(part)
        parts[c["ref"]] = part

    def sources(net: str) -> list[dict]:
        found = []
        for ref, part in parts.items():
            if part.kind == "ldo":
                vo = part.pad_named("VO", "VOUT", "OUT")
                if vo and part.pads.get(vo) == net:
                    found += [p for p in pads_of.get((ref, net), []) if p["name"] == vo]
            elif part.kind == "converter":
                sw = part.pad_named("SW", "LX", "PH", "SWITCH")
                sw_net = part.pads.get(sw) if sw else None
                for lref, nets in nets_of.items():
                    if lref.startswith("L") and sw_net and {sw_net, net} <= nets and parts.get(lref) \
                            and parts[lref].kind == "inductor":
                        found += pads_of.get((lref, net), [])
                vo = part.pad_named("VOUT", "OUT")
                if not found and vo and part.pads.get(vo) == net:
                    found += [p for p in pads_of.get((ref, net), []) if p["name"] == vo]
        if not found:      # fed from off the board
            for ref, part in parts.items():
                if part.kind == "connector":
                    found += pads_of.get((ref, net), [])
        return found

    def loads(net: str) -> dict[tuple, float]:
        """(ref, pad name) -> amps drawn there."""
        draw: dict[tuple, float] = {}

        def share(ref: str, amps: float, names: list[str] | None = None):
            pins = [p for p in pads_of.get((ref, net), []) if names is None or p["name"] in names]
            for p in pins:
                draw[(ref, p["name"])] = draw.get((ref, p["name"]), 0.0) + amps / len(pins)
        for ld in sim_res.get("loads") or []:
            if net in (ld.get("nets") or []):
                share(ld["ref"], float(ld.get("peak_ma") or ld.get("typ_ma") or 0) / 1000 / len(ld["nets"]))
        for ref, row in reg_rows.items():
            part = parts.get(ref)
            vi = part.pad_named("VIN", "VI", "IN", "VCC", "PVIN") if part else None
            if not vi or part.pads.get(vi) != net:
                continue
            iout = max(float(row.get("current_ma") or 0), float(row.get("peak_ma") or 0)) / 1000
            if row["kind"] == "converter":
                iout = iout * float(row.get("vout") or 0) / (0.85 * max(float(row.get("vin") or 0), 0.5))
            share(ref, iout, [vi] if any(p["name"] == vi for p in pads_of.get((ref, net), [])) else None)
        return draw

    for net in sorted({t["net"] for t in geo.get("tracks") or [] if t.get("net")}):
        if net in poured or not nl.is_power(net):
            continue
        v0 = rail_v.get(net) or sim.net_volts(net)
        src, draw = sources(net), loads(net)
        if not v0 or not src or not draw:
            continue
        row = _rail(net, float(v0), geo, src, draw, pads_of, board_mm)
        if row is None:
            continue
        out["rails"].append(row)
        w = row["worst"]
        if w and w["drop_mv"] / 1000 > limit / 100 * row["volts"]:
            out["warnings"].append(
                f"{net}: {w['ref']} pin {w['pad']} gets {w['volts']:.3f} V, {w['drop_mv']:.0f} mV "
                f"({w['drop_mv'] / 10 / row['volts']:.1f} %) below the {row['volts']:g} V at "
                f"{', '.join(row['sources'])}, through the routed copper at {row['current_ma']:.0f} mA. Widen the "
                f"track, add a parallel path or a plane (limit {limit:g} %, rules.rail_drop_pct)")
        if row["unreached"]:
            out["warnings"].append(f"{net}: the tracks don't join {', '.join(row['unreached'][:5])} to "
                                   f"{', '.join(row['sources'])}, so their voltage was not worked out")
    return out


def _rail(net, v0, geo, src, draw, pads_of, board_mm) -> dict | None:
    segs = [t for t in (geo.get("tracks") or []) + (geo.get("arcs") or []) if t.get("net") == net]
    net_ = _Network()
    # a track that another one tees into part-way along is split there, so the tee is a node
    ends_by_layer: dict[str, set] = {}
    for t in segs:
        for p in (t["a"], t["b"]):
            ends_by_layer.setdefault(t["layer"], set()).add((round(p[0], 3), round(p[1], 3)))
    for t in segs:
        if "m" in t:
            net_.link(_key(t["a"], t["layer"]), _key(t["b"], t["layer"]), _track_r(_arc_len(t), t["w"], t["layer"]))
            continue
        a, b = (round(t["a"][0], 3), round(t["a"][1], 3)), (round(t["b"][0], 3), round(t["b"][1], 3))
        L = math.dist(a, b)
        cut = sorted((math.dist(a, p), p) for p in ends_by_layer[t["layer"]] if p not in (a, b) and L > 0
                     and abs(math.dist(a, p) + math.dist(p, b) - L) < 0.005)
        pts = [a, *(p for _, p in cut), b]
        for p, q in zip(pts, pts[1:]):
            net_.link((*p, t["layer"]), (*q, t["layer"]), _track_r(math.dist(p, q), t["w"], t["layer"]))
    nodes = list(net_.adj)
    for i, v in enumerate(v for v in geo.get("vias") or [] if v.get("net") == net):
        r = (v.get("d") or 0.6) / 2
        for n in nodes:
            if math.hypot(n[0] - v["p"][0], n[1] - v["p"][1]) <= r + 0.01:
                net_.link(("via", i), n, _via_r(v.get("drill") or 0.3, board_mm) / 2)

    def pad_node(p: dict):
        return ("pad", p["ref"], p["name"], p["x"], p["y"])
    attached = set()
    all_pads = [p for (_, n), ps in pads_of.items() if n == net for p in ps]
    for p in all_pads:
        reach = max(p["w"], p["h"]) / 2 + 0.01
        layers = None if _through(p) else {"F.Cu" if p.get("front", True) else "B.Cu"}
        for n in nodes:
            if (layers is None or n[2] in layers) and math.hypot(n[0] - p["x"], n[1] - p["y"]) <= reach:
                net_.link(pad_node(p), n, 1e-6)
                attached.add(pad_node(p))
    source = ("source",)
    src_names = []
    for p in src:
        if pad_node(p) in attached:
            net_.link(source, pad_node(p), 1e-9)
            src_names.append(f"{p['ref']}.{p['name']}")
    if not src_names:
        return None
    sinks: dict = {}
    for (ref, name), amps in draw.items():
        pins = [p for p in pads_of.get((ref, net), []) if p["name"] == name and pad_node(p) in attached]
        for p in pins:
            sinks[pad_node(p)] = sinks.get(pad_node(p), 0.0) + amps / len(pins)
    d = net_.drops(source, sinks)
    pins, unreached = [], []
    for node, amps in sinks.items():
        ref, name = node[1], node[2]
        if node not in d:
            unreached.append(f"{ref}.{name}")
            continue
        pins.append({"ref": ref, "pad": name, "ma": round(amps * 1000, 1), "drop_mv": round(d[node] * 1000, 2),
                     "volts": round(v0 - d[node], 4)})
    for (ref, name) in draw:
        if not any(pad_node(p) in attached for p in pads_of.get((ref, net), []) if p["name"] == name):
            unreached.append(f"{ref}.{name}")       # no track reaches the pin at all
    pins.sort(key=lambda r: -r["drop_mv"])
    return {"net": net, "volts": round(v0, 4), "sources": sorted(set(src_names)),
            "current_ma": round(sum(draw.values()) * 1000, 1), "pins": pins[:40],
            "worst": pins[0] if pins else None, "unreached": sorted(set(unreached))}
