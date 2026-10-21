"""Sim Lab: the circuit simulation laid over the finished board in 3D.

`scene` gives the browser everything it draws: the outline, copper, part bodies, where current flows on each
track, what every part dissipates and how bright each LED is, the risks the simulation and board checks found,
and the parameters a user can change with sliders. `whatif` re-simulates the design with changed parameters
(a resistor value, a supply voltage, an IC's load current, the ambient temperature) and returns the new numbers.
The thermal map itself is solved in the browser (web/simlab.js) from the heat each part gives off, so the
airflow and copper sliders respond instantly.
"""

from __future__ import annotations

import copy
import heapq
import math
import re
from pathlib import Path

from . import design, enclosure, power, sim

LED_COLORS = {"red": [255, 50, 35], "green": [40, 235, 90], "blue": [50, 110, 255], "white": [255, 245, 230],
              "yellow": [255, 210, 40], "amber": [255, 160, 20], "orange": [255, 120, 20], "pink": [255, 90, 170],
              "uv": [170, 80, 255], "ir": [120, 30, 30]}
VF_GUESS = {"red": 2.0, "yellow": 2.1, "amber": 2.1, "orange": 2.1, "green": 3.0, "blue": 3.0, "white": 3.0}
REF_RE = re.compile(r"\b([A-Z]{1,4}\d{1,4})\b")
AIRFLOW = [(8, "still air, inside a closed box"), (12, "still air"), (25, "gentle airflow"), (60, "a fan")]


def _is_led(c: dict) -> bool:
    blob = " ".join(str(c.get(k) or "") for k in ("ref", "value", "description", "footprint", "symbol")).lower()
    return "led" in blob and not re.search(r"driver|controller", str(c.get("description") or "").lower()) \
        and not str(c.get("ref", "")).upper().startswith(("U", "IC"))


def _colour_word(text: str) -> str | None:
    t = (text or "").lower()
    for w in ("red", "green", "blue", "white", "yellow", "amber", "orange", "pink", "uv", "ir"):
        if re.search(rf"\b{w}\b", t):
            return w
    return None


# ------------------------------------------------------------------------------------ geometry
def outline_polygon(geo: dict) -> list[list[float]]:
    """The board edge as one closed polygon (arcs sampled), in board coordinates."""
    segs = []
    for o in geo.get("outline") or []:
        if o["kind"] == "line":
            segs.append([o["start"], o["end"]])
        elif o["kind"] == "arc":
            segs.append(_arc_points(o["start"], o["mid"], o["end"]))
        elif o["kind"] == "rect":
            (x0, y0), (x1, y1) = o["start"], o["end"]
            return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        elif o["kind"] == "circle":
            cx, cy = o["center"]
            r = math.dist(o["center"], o["end"])
            return [[cx + r * math.cos(a * math.pi / 24), cy + r * math.sin(a * math.pi / 24)] for a in range(48)]
    if not segs:
        bb = geo.get("bbox") or [0, 0, 0, 0]
        return [[bb[0], bb[1]], [bb[2], bb[1]], [bb[2], bb[3]], [bb[0], bb[3]]]
    poly = list(segs.pop(0))
    while segs:
        end = poly[-1]
        k = min(range(len(segs)), key=lambda i: min(math.dist(end, segs[i][0]), math.dist(end, segs[i][-1])))
        s = segs.pop(k)
        if math.dist(end, s[-1]) < math.dist(end, s[0]):
            s = s[::-1]
        poly += s[1:]
    if len(poly) > 2 and math.dist(poly[0], poly[-1]) < 1e-3:
        poly.pop()
    return [[round(x, 3), round(y, 3)] for x, y in poly]


def _arc_points(a, m, b, n: int = 10) -> list[list[float]]:
    (x1, y1), (x2, y2), (x3, y3) = a, m, b
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        return [list(a), list(b)]
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1) + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3) + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a1, a2, a3 = (math.atan2(y - uy, x - ux) for x, y in (a, m, b))
    sweep = (a3 - a1) % (2 * math.pi)
    if (a2 - a1) % (2 * math.pi) > sweep:          # the arc runs the other way round
        sweep -= 2 * math.pi
    return [[ux + r * math.cos(a1 + sweep * i / n), uy + r * math.sin(a1 + sweep * i / n)] for i in range(n + 1)]


def _key(p) -> tuple[int, int]:
    return round(p[0] * 50), round(p[1] * 50)


def flows(geo: dict, roots: dict[str, list[tuple[float, float, float, float]]], sinks: set[str]) -> list[dict]:
    """Each track with the direction current flows along it: away from the net's source pads, or towards them
    for return nets (ground). roots maps a net to the pad boxes (x0, y0, x1, y1) its current comes from."""
    by_net: dict[str, list[int]] = {}
    tracks = geo.get("tracks") or []
    for i, t in enumerate(tracks):
        by_net.setdefault(t["net"], []).append(i)
    out = [None] * len(tracks)
    for net, idx in by_net.items():
        adj: dict[tuple, list[tuple[tuple, float]]] = {}
        for i in idx:
            a, b = _key(tracks[i]["a"]), _key(tracks[i]["b"])
            ln = math.dist(tracks[i]["a"], tracks[i]["b"])
            adj.setdefault(a, []).append((b, ln))
            adj.setdefault(b, []).append((a, ln))
        dist: dict[tuple, float] = {}
        heap = []
        for k in adj:
            x, y = k[0] / 50, k[1] / 50
            if any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in roots.get(net, [])):
                dist[k] = 0.0
                heap.append((0.0, k))
        if not heap and adj:                       # no known source: start from one end so dashes still run
            k = min(adj)
            dist[k] = 0.0
            heap.append((0.0, k))
        heapq.heapify(heap)
        while heap:
            d, k = heapq.heappop(heap)
            if d > dist.get(k, math.inf):
                continue
            for n, ln in adj[k]:
                if d + ln < dist.get(n, math.inf):
                    dist[n] = d + ln
                    heapq.heappush(heap, (d + ln, n))
        for i in idx:
            t = tracks[i]
            da, db = dist.get(_key(t["a"]), math.inf), dist.get(_key(t["b"]), math.inf)
            d = 1 if da <= db else -1
            out[i] = {**t, "dir": -d if net in sinks else d}
    return out


# ------------------------------------------------------------------------------------ simulation
def _supply_volts(res: dict) -> dict[str, float]:
    v = {s["net"]: s.get("volts") for s in res.get("supplies") or [] if s.get("volts") is not None}
    v.update({r["net"]: r["volts"] for r in res.get("rails") or []})
    return v


def part_state(spec: dict, res: dict) -> dict[str, dict]:
    """Per part: heat given off (mW), current, the simulation's temperature estimate and LED light."""
    comps = {c["ref"]: c for c in spec["components"]}
    rows = {r["ref"]: r for r in res.get("parts") or []}
    pins: dict[str, dict[str, str]] = {}
    net_pins: dict[str, list[str]] = {}
    for n in spec["nets"]:
        net_pins[n["name"]] = n["pins"]
        for pin in n["pins"]:
            ref, _, pad = pin.partition(".")
            pins.setdefault(ref, {})[pad] = n["name"]
    volts = _supply_volts(res)
    ground = {n["name"] for n in spec["nets"] if sim.GND_RE.match(n["name"])}
    out: dict[str, dict] = {}
    for ref, c in comps.items():
        r = rows.get(ref) or {}
        st: dict = {"kind": r.get("kind") or "", "current_ma": r.get("current_ma"), "temp_c": r.get("temp_c")}
        k = r.get("kind")
        mw = None
        if r.get("power_mw") is not None and k != "converter":
            mw = r["power_mw"]
        elif k == "led" and r.get("current_ma"):
            mw = (r.get("vf") or 2.0) * r["current_ma"]
        elif k == "ic" and r.get("current_ma") is not None:
            mw = r["current_ma"] * (r.get("supply_v") or 3.3)
        elif k == "converter" and r.get("vout") and r.get("current_ma"):
            mw = r["vout"] * r["current_ma"] * (1 / 0.85 - 1)
        if _is_led(c):
            light, led_mw = _led_light(c, r, pins.get(ref, {}), net_pins, rows, comps, volts, ground)
            if light:
                st["light"] = light
                mw = mw if mw else led_mw
        st["power_mw"] = round(mw, 3) if mw else 0.0
        st["theta_jb"] = round(0.35 * sim._theta(c.get("footprint") or ""), 1)
        out[ref] = st
    return out


def _led_light(c, row, pads, net_pins, rows, comps, volts, ground) -> tuple[list[dict], float]:
    """LED channels: colour and current. Single LEDs come from the simulation; multi-colour LEDs modelled with
    custom SPICE get each channel's current from the resistor in series with it."""
    base = _colour_word(f"{c.get('value')} {c.get('description')}") or "white"
    if row.get("kind") == "led":
        ma = row.get("current_ma") or 0
        return [{"color": LED_COLORS.get(base, LED_COLORS["white"]), "ma": round(ma, 3),
                 "max_ma": row.get("max_ma") or 20}], (row.get("vf") or VF_GUESS.get(base, 2.0)) * ma
    names = {p["number"]: p["name"] for p in sim._symbol_pins(c.get("symbol"))}
    chans, mw = [], 0.0
    for pad, net in pads.items():
        if net in volts or net in ground:
            continue
        ma, word = 0.0, None
        for pin in net_pins.get(net, []):
            ref = pin.partition(".")[0]
            rr = rows.get(ref)
            if rr and rr.get("kind") == "resistor":
                ma += rr.get("current_ma") or 0
                word = word or _colour_word(comps.get(ref, {}).get("description") or "")
        nm = str(names.get(pad, "")).upper()
        word = {"RK": "red", "GK": "green", "BK": "blue", "R": "red", "G": "green", "B": "blue",
                "WK": "white"}.get(nm) or word or base
        chans.append({"color": LED_COLORS.get(word, LED_COLORS["white"]), "ma": round(ma, 3), "max_ma": 20})
        mw += VF_GUESS.get(word, 2.0) * ma
    return chans, mw


# ------------------------------------------------------------------------------------ parameters
def _ohms(v: float) -> str:
    for div, suf in ((1e6, "M"), (1e3, "k")):
        if v >= div:
            return f"{v / div:.3g}{suf}"
    return f"{v:.3g}R"


def params(spec: dict, res: dict) -> list[dict]:
    """Every slider the Sim Lab offers. `local` ones only change the thermal map in the browser."""
    out = [{"id": "ambient", "label": "Ambient temperature", "unit": "°C", "min": -20, "max": 85, "step": 1,
            "value": float(res.get("ambient_c") or (spec.get("simulation") or {}).get("ambient_c") or 25)},
           {"id": "airflow", "label": "Cooling", "unit": "W/m²K", "min": 6, "max": 60, "step": 1, "value": 12,
            "local": True, "marks": AIRFLOW},
           {"id": "copper", "label": "Copper weight", "unit": "oz", "min": 0.5, "max": 2, "step": 0.5, "value": 1,
            "local": True}]
    comps = {c["ref"]: c for c in spec["components"]}
    for s in res.get("supplies") or []:
        if s["kind"] == "signal" or not s.get("volts"):
            continue
        v = float(s["volts"])
        out.append({"id": f"src:{s['net']}", "label": f"{s['net']} supply ({s['kind']})", "unit": "V",
                    "min": round(v * 0.5, 2), "max": round(v * 1.6, 2), "step": 0.05, "value": v})
    for r in res.get("parts") or []:
        c = comps.get(r["ref"]) or {}
        if r["kind"] == "resistor":
            v = sim.parse_value(c.get("value"))
            if v:
                desc = c.get("description") or ""
                out.append({"id": f"r:{r['ref']}", "label": f"{r['ref']} {c.get('value')}" + (f" ({desc})" if desc else ""),
                            "unit": "Ω", "min": v / 10, "max": v * 10, "value": v, "log": True, "ref": r["ref"]})
        elif r["kind"] == "ic" and r.get("current_ma"):
            v = float(r["current_ma"])
            out.append({"id": f"load:{r['ref']}", "label": f"{r['ref']} {c.get('value') or ''} supply current",
                        "unit": "mA", "min": 0, "max": round(max(5 * v, 50)), "step": 0.5, "value": v, "ref": r["ref"]})
    return out


def apply(spec: dict, res: dict, values: dict) -> dict:
    """A copy of the design with the slider values put in."""
    spec = copy.deepcopy(spec)
    comps = {c["ref"]: c for c in spec["components"]}
    simsec = spec.setdefault("simulation", {})
    for pid, val in (values or {}).items():
        val = float(val)
        if pid == "ambient":
            simsec["ambient_c"] = val
        elif pid.startswith("src:"):
            net = pid[4:]
            srcs = simsec.setdefault("sources", [])
            if not any(s.get("kind") != "signal" for s in srcs):
                # the supplies were guessed; write them all down, or the others would no longer be guessed
                for s in res.get("supplies") or []:
                    if s["kind"] != "signal":
                        srcs.append({k: s[k] for k in ("net", "volts", "kind", "max_ma") if s.get(k) is not None})
            for s in srcs:
                if s.get("net") == net:
                    s["volts"] = val
        elif pid.startswith("r:") and pid[2:] in comps:
            comps[pid[2:]]["value"] = _ohms(val)
        elif pid.startswith("load:") and pid[5:] in comps:
            s = comps[pid[5:]].setdefault("sim", {})
            s["load_ma"] = val
            s["peak_ma"] = max(val, float(s.get("peak_ma") or 0))
    return spec


# ------------------------------------------------------------------------------------ risks
def risks(spec: dict, res: dict, budget: dict | None, checks: dict | None, plist: list[dict],
          pstate: dict[str, dict] | None = None) -> list[dict]:
    """Everything worth a look, most serious first, each tied to parts and to the sliders that change it."""
    refs = {c["ref"] for c in spec["components"]}
    nets = {n["name"] for n in spec["nets"]}
    by_ref = {p.get("ref"): p["id"] for p in plist if p.get("ref")}
    pids = {p["id"] for p in plist}
    series = _series_resistors(spec)
    out, seen = [], set()

    def add(level, source, text, extra_refs=(), extra_params=()):
        if text in seen:
            return
        seen.add(text)
        rs = [r for r in dict.fromkeys(REF_RE.findall(text) + list(extra_refs)) if r in refs]
        ns = [n for n in nets if re.search(rf"(?<![\w+]){re.escape(n)}(?!\w)", text)]
        ps = []
        for r in rs:
            ps += [by_ref[x] for x in [r] + series.get(r, []) if x in by_ref]
        ps += [f"src:{n}" for n in ns if f"src:{n}" in pids]
        if re.search(r"°C|hot|heat|dissipat|thermal|temperature", text, re.I):
            ps += ["ambient", "airflow", "copper"]
        ps += list(extra_params)
        if not ps:
            ps = ["ambient"] + [p["id"] for p in plist if p["id"].startswith("src:")]
        out.append({"id": f"k{len(out) + 1}", "level": level, "source": source, "text": text, "refs": rs,
                    "nets": ns, "params": list(dict.fromkeys(p for p in ps if p in pids))})

    for t in res.get("problems") or []:
        add("problem", "Simulation", t)
    for t in (checks or {}).get("problems") or []:
        add("problem", "Board check", t)
    names = {tuple(v): k for k, v in LED_COLORS.items()}
    led_warn = []
    for ref, st in (pstate or {}).items():
        for ch in st.get("light") or []:
            colour = names.get(tuple(ch["color"]), "")
            if ch["ma"] > ch["max_ma"]:
                add("problem", "LEDs", f"{ref} {colour} carries {ch['ma']:.1f} mA, over the {ch['max_ma']:g} mA "
                                       f"an LED like it is rated for: raise its series resistor")
            elif ch["ma"] > 0.85 * ch["max_ma"]:
                led_warn.append(f"{ref} {colour} runs at {ch['ma']:.1f} mA, close to its {ch['max_ma']:g} mA limit")
    for t in res.get("warnings") or []:
        add("warning", "Simulation", t)
    for t in led_warn:
        add("warning", "LEDs", t)
    for t in (budget or {}).get("notes") or []:
        add("warning", "Power budget", t)
    for t in (checks or {}).get("warnings") or []:
        add("warning", "Board check", t)
    # things to keep an eye on, so every board has something to explore
    for s in (budget or {}).get("supplies") or []:
        if s.get("use_pct") is not None:
            add("watch", "Power budget", f"{s['net']} supplies {s['peak_ma']:.0f} mA at peak of the {s['max_ma']:g} mA "
                                         f"it can give ({s['use_pct']:.0f}%)")
    for th in ((budget or {}).get("thermal") or [])[:3]:
        add("watch", "Heat", f"{th['ref']} ({th.get('value') or th['kind']}) dissipates {th['power_mw']:.0f} mW, "
                             f"about {th['temp_c']:.0f} °C ({th['margin_c']:.0f} °C below its {th['limit_c']} °C limit)")
    if not (budget or {}).get("thermal"):
        hot = sorted(((st["power_mw"], ref) for ref, st in (pstate or {}).items() if st.get("power_mw")), reverse=True)
        for i, (mw, ref) in enumerate(hot[:3]):
            rank = "the most of any part" if i == 0 else f"number {i + 1} on the board"
            add("watch", "Heat", f"{ref} gives off {mw:.0f} mW of heat, {rank}", extra_params=["ambient", "airflow"])
    order = {"problem": 0, "warning": 1, "watch": 2}
    out.sort(key=lambda r: order[r["level"]])
    return out


def _series_resistors(spec: dict) -> dict[str, list[str]]:
    """For each part, the resistors that share a non-power net with it (an LED's current-limiting resistor)."""
    out: dict[str, list[str]] = {}
    for n in spec["nets"]:
        if n.get("class") == "power" or sim.GND_RE.match(n["name"]):
            continue
        refs = [p.partition(".")[0] for p in n["pins"]]
        res_ = [r for r in refs if re.match(r"R\d", r)]
        for r in refs:
            out.setdefault(r, [])
            out[r] += [x for x in res_ if x != r and x not in out[r]]
    return out


# ------------------------------------------------------------------------------------ scene
def dynamic(spec: dict, res: dict, checks: dict | None) -> dict:
    """The parts of the scene a re-simulation changes."""
    budget = power.budget(spec, res) if res.get("ran") else None
    plist = params(spec, res)
    rows = res.get("net_current_ma") or {}
    pstate = part_state(spec, res)
    ids = {p["id"] for p in plist}
    links = {r: [x for x in [f"r:{r}", f"load:{r}"] + [f"r:{s}" for s in series] if x in ids]
             for r, series in _series_resistors(spec).items()}
    return {"ran": bool(res.get("ran")), "state": pstate, "params": plist, "links": {k: v for k, v in links.items() if v},
            "risks": risks(spec, res, budget, checks, plist, pstate),
            "supplies": [s for s in res.get("supplies") or [] if s["kind"] != "signal"],
            "net_ma": rows, "ambient_c": float(res.get("ambient_c") or 25),
            "problems": res.get("problems") or [], "warnings": res.get("warnings") or []}


def scene(spec: dict, geo: dict, res: dict, checks: dict | None = None) -> dict:
    """Everything the 3D Sim Lab draws, in mm from the board's top-left corner."""
    spec = design.normalise(spec)
    bb = geo.get("bbox") or [0, 0, spec["board"]["width_mm"], spec["board"]["height_mm"]]
    ox, oy = bb[0], bb[1]

    def P(p):
        return [round(p[0] - ox, 3), round(p[1] - oy, 3)]

    ctx = enclosure.context(geo, spec)
    parts = []
    for p in ctx["parts"]:
        x0, y0, x1, y1 = p["courtyard_mm"]
        inset = min(0.25, (x1 - x0) / 6, (y1 - y0) / 6)      # courtyards are a little bigger than the body
        parts.append({"ref": p["ref"], "value": p["value"], "box": [x0 + inset, y0 + inset, x1 - inset, y1 - inset],
                      "h": round(p["height_mm"], 2), "side": p["side"], "x": p["x_mm"], "y": p["y_mm"],
                      "rot": p["rotation"], "footprint": p["footprint"]})
    holes = [{"x": h["x_mm"], "y": h["y_mm"], "d": h["diameter_mm"]} for h in ctx["mounting_holes"]]

    # where each net's current comes from: the supply's connector pads, a regulator's output, an IC pin
    comps = {c["ref"]: c for c in spec["components"]}
    supply_nets = {s["net"] for s in res.get("supplies") or [] if s["kind"] != "signal"}
    reg_out = {}
    for r in res.get("parts") or []:
        if r["kind"] in ("ldo", "converter"):
            reg_out[r["ref"]] = True
    ground = {n["name"] for n in spec["nets"] if sim.GND_RE.match(n["name"])}
    roots: dict[str, list] = {}
    for pad in geo["pads"]:
        net, ref = pad["net"], pad["ref"]
        if not net:
            continue
        half = max(pad["w"], pad["h"]) / 2 + 0.05
        box = (pad["x"] - half, pad["y"] - half, pad["x"] + half, pad["y"] + half)
        src_conn = re.match(r"(J|BT|P|CN)\d", ref) and (net in supply_nets or net in ground)
        if src_conn or ref in reg_out or (net not in supply_nets and net not in ground and re.match(r"(U|IC)\d", ref)):
            roots.setdefault(net, []).append(box)
    tracks = []
    for t in flows(geo, roots, ground):
        tracks.append({"a": P(t["a"]), "b": P(t["b"]), "w": t["w"], "layer": t["layer"], "net": t["net"],
                       "dir": t["dir"]})
    net_kind = {n["name"]: ("ground" if n["name"] in ground else "power" if n.get("class") == "power"
                            or n["name"] in supply_nets else "signal") for n in spec["nets"]}
    return {
        "name": spec.get("name"), "board": {"w": round(bb[2] - bb[0], 3), "h": round(bb[3] - bb[1], 3),
                                            "t": float(spec["board"].get("thickness_mm") or 1.6),
                                            "layers": int(spec["board"].get("layers") or 2)},
        "outline": [P(p) for p in outline_polygon(geo)], "holes": holes, "parts": parts,
        "pads": [{"ref": p["ref"], "x": round(p["x"] - ox, 3), "y": round(p["y"] - oy, 3), "w": p["w"], "h": p["h"],
                  "rot": p["rot"], "shape": p["shape"], "front": p["front"], "back": p["back"], "net": p["net"],
                  "drill": p["drill"]} for p in geo["pads"]],
        "tracks": tracks, "vias": [{"p": P(v["p"]), "d": v["d"], "net": v["net"]} for v in geo.get("vias") or []],
        "zones": [{"layer": z["layer"], "net": z["net"], "pts": [P(q) for q in z["pts"]]} for z in geo.get("zones") or []],
        "net_kind": net_kind, "net_volts": _supply_volts(res),
        "descriptions": {r: comps[r].get("description") or "" for r in comps},
        **dynamic(spec, res, checks),
    }


def whatif(spec: dict, base: dict, values: dict, out_dir: Path, checks: dict | None = None, **sim_kw) -> dict:
    """Re-simulate with the slider values and return the changed parts of the scene."""
    spec = design.normalise(spec)
    changed = apply(spec, base, {k: v for k, v in (values or {}).items() if not k.startswith(("airflow", "copper"))})
    res = sim.simulate(changed, out_dir, **sim_kw)
    return dynamic(changed, res, checks)
