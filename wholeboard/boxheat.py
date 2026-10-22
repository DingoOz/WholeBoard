"""Heat in an enclosure: how warm the air inside the box gets, and how hot its surfaces are to the touch.

The board's own heat map (web/simlab.js) is solved for the air around it. Put the board in a box and that air
is warmer and barely moves. This is a lumped network of the box, good to a few degrees for small cases, with a
node for the board, the inside air, and the inside and outside of the lid, the side walls and the base:

  board → air          weak natural convection, held back by the box
  board → walls        radiation straight to the inside of each wall
  air → walls          natural convection over each wall's inside surface
  through the walls    conduction: a little for printed plastic, nothing for aluminium; heat also spreads
                       round the box from one face to the next, which makes a metal box one temperature
  walls → room         natural convection and radiation from each outside face; a base on a desk loses little
  air → room           through the openings, by the chimney effect: warm air leaves by lid slots and holes,
                       cooler air comes in low down (side vents, the gaps round the connectors). With openings
                       at one height only, air swaps through them both ways, much more slowly.

The board sees a mix of the inside air and the walls, so `solve` gives the temperature that mix amounts to
(`board_env_c`) and how strongly the board loses heat to it (`board_h`): the browser's plate solver and the
circuit simulation use those in place of open air. `geometry` finds the box (the printed enclosure, the stock
case, or the one WholeBoard would suggest) in board coordinates.
"""

from __future__ import annotations

import math

from . import enclosure

SIGMA = 5.67e-8
G = 9.81
RHO_CP = 1.16 * 1007          # air, J/m³K
CD = 0.6                      # discharge coefficient of a slot
# wall material: conductivity (W/mK) and how well its surface radiates
MATERIALS = {"PLA": (0.13, 0.9), "PETG": (0.2, 0.9), "ABS": (0.17, 0.9), "ASA": (0.17, 0.9),
             "PC": (0.2, 0.9), "aluminium": (160, 0.3), "aluminium (painted)": (160, 0.85)}
VENTS = ("design", "none", "lid", "sides", "both")
VENT_NAMES = {"design": "as designed", "none": "sealed", "lid": "lid slots", "sides": "side slots",
              "both": "lid and side slots"}
PLACEMENTS = {"desk": "on a desk", "raised": "on feet or hanging"}
BOARD_EMISSIVITY = 0.9
PORT_GAP = 0.8                # the gap round a connector in its opening, mm
DESK_H = 3.0                  # W/m²K: a desk takes a little heat from the base it stands on


# ------------------------------------------------------------------------------------ geometry
def geometry(spec: dict, geo: dict, enc_spec: dict | None, cfg: dict | None = None) -> dict | None:
    """The box around the board, in mm: x and y as on the board (from its top-left), z up from the board's top."""
    board = spec.get("board") or {}
    try:
        ctx = enclosure.context(geo, spec, (enc_spec or {}).get("part_heights"))
    except ValueError:
        return None
    if board.get("case"):
        return _from_case(board["case"], ctx)
    kind = "printed" if enc_spec else "suggested"
    enc = enc_spec or enclosure.suggest(ctx)
    material = str(((cfg or {}).get("printing") or {}).get("material") or "PLA").upper()
    return _from_job(enclosure.resolve(ctx, enc), kind, material if material in MATERIALS else "PLA",
                     bool(enc.get("rubber_feet")))


def _from_job(job: dict, kind: str, material: str, feet: bool) -> dict:
    b = job["board"]
    bx, by_top = b["x"], b["y"] + b["h"]          # enclosure X,Y of the board's (0, 0) corner
    zt = job["z_top"]
    ow, oh = job["outer"]
    height = job["base_h"] + job["lid_t"]

    def along(wall, u):     # position along a wall, in board x (top/bottom walls) or y (left/right walls)
        return by_top - u if wall in ("left", "right") else u - bx
    side = []
    for s in job["side_slots"]:
        a, c = sorted((along(s["wall"], s["u0"]), along(s["wall"], s["u1"])))
        side.append({"wall": s["wall"], "a": a, "b": c, "z0": s["z0"] - zt, "z1": s["z1"] - zt, "open": 1.0})
    for o in job["openings"]:
        a, c = sorted((along(o["wall"], o["u0"]), along(o["wall"], o["u1"])))
        w, h = c - a, o["z1"] - o["z0"]
        frac = 0.5 if o.get("name") in ("custom",) or not o.get("name") else \
            max(0.0, 1 - max(0.0, w - 2 * PORT_GAP) * max(0.0, h - 2 * PORT_GAP) / max(w * h, 1e-6))
        side.append({"wall": o["wall"], "a": a, "b": c, "z0": o["z0"] - zt, "z1": o["z1"] - zt, "open": round(frac, 3),
                     "name": o.get("name")})
    lid = [[s["x"] - bx, by_top - s["y"] - s["h"], s["x"] + s["w"] - bx, by_top - s["y"]] for s in job["lid_slots"]]
    holes = []
    for hh in job.get("lid_holes") or []:
        w, h = (hh["d"], hh["d"]) if "d" in hh else (hh["w"], hh["h"])
        holes.append([hh["x"] - bx - w / 2, by_top - hh["y"] - h / 2, hh["x"] - bx + w / 2, by_top - hh["y"] + h / 2])
    return {"kind": kind, "name": {"printed": "the printed enclosure", "suggested": "a printed box"}[kind],
            "outer": [round(-bx, 2), round(by_top - oh, 2), round(ow - bx, 2), round(by_top, 2)],
            "z0": round(-zt, 2), "z1": round(height - zt, 2), "wall": job["wall"], "floor": job["floor"],
            "lid": job["lid_t"], "material": material, "feet": feet, "board_mm": [b["w"], b["h"]],
            "lid_slots": lid, "lid_holes": holes, "side": side,
            "designed_vents": {"lid": bool(job["lid_slots"]), "sides": bool(job["side_slots"])}}


def _from_case(case: dict, ctx: dict) -> dict:
    from . import case as stock_case
    c = stock_case.layout({"board": {"case": case}}, ctx)
    zt = c["standoff"] + c["thick"]
    wall, floor = c["wall"], c["floor"]
    x0, y0 = -c["ax"] - wall, -c["ay"] - wall
    side = []
    ops = stock_case.openings({"board": {"case": case}}, ctx)["faces"]
    for face, wall_name in (("front", "bottom"), ("back", "top"), ("left", "left"), ("right", "right")):
        for o in ops[face]:
            w, h = o["w"], o["h"]
            frac = max(0.0, 1 - max(0.0, w - 2 * stock_case.MARGIN) * max(0.0, h - 2 * stock_case.MARGIN) / (w * h))
            if face == "back":
                a = c["L"] - o["u"] - w - c["ax"]
            elif face == "front":
                a = o["u"] - c["ax"]
            elif face == "left":
                a = o["u"] - c["ay"]
            else:
                a = c["W"] - o["u"] - w - c["ay"]
            side.append({"wall": wall_name, "a": a, "b": a + w, "z0": o["v"] - zt, "z1": o["v"] + h - zt,
                         "open": round(frac, 3), "name": o["ref"]})
    holes = [[o["u"] - c["ax"] - o["d"] / 2, c["W"] - o["v"] - c["ay"] - o["d"] / 2,
              o["u"] - c["ax"] + o["d"] / 2, c["W"] - o["v"] - c["ay"] + o["d"] / 2] for o in ops["lid"]]
    mat = str(case.get("material") or "ABS")
    mat = next((m for m in MATERIALS if m.lower() == mat.lower()), "aluminium" if "alu" in mat.lower() else "ABS")
    return {"kind": "case", "name": f"the {c['name']}", "outer": [round(x0, 2), round(y0, 2),
            round(x0 + c["L"] + 2 * wall, 2), round(y0 + c["W"] + 2 * wall, 2)],
            "z0": round(-zt - floor, 2), "z1": round(c["H"] + wall - zt, 2), "wall": wall, "floor": floor,
            "lid": wall, "material": mat, "feet": False, "board_mm": [c["bw"], c["bh"]],
            "lid_slots": [], "lid_holes": holes, "side": side,
            "designed_vents": {"lid": False, "sides": False}}


def vent_slots(box: dict, which: str) -> tuple[list, list]:
    """The lid and side slots for a vent choice: the design's own, or the ones WholeBoard would cut."""
    if which == "design":
        return box["lid_slots"], [s for s in box["side"] if not s.get("name")]
    lid, side = [], []
    x0, y0, x1, y1 = box["outer"]
    ow, oh = x1 - x0, y1 - y0
    if which in ("lid", "both"):
        if box["kind"] != "case" and box["lid_slots"]:
            lid = box["lid_slots"]
        else:
            sl = max(6.0, min(14.0, oh * 0.45))
            n = int((ow * 0.6) // 4.0)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            lid = [[cx + (i - (n - 1) / 2) * 4 - 0.8, cy - sl / 2, cx + (i - (n - 1) / 2) * 4 + 0.8, cy + sl / 2]
                   for i in range(n)]
    if which in ("sides", "both"):
        if box["kind"] != "case" and any(not s.get("name") for s in box["side"]):
            side = [s for s in box["side"] if not s.get("name")]
        else:
            z0, z1 = 1.5, min(box["z1"] - box["lid"] - 2.0, 10.0)
            if z1 - z0 >= 3:
                for wall, lo, length in (("left", y0, oh), ("right", y0, oh), ("top", x0, ow), ("bottom", x0, ow)):
                    n = int((length * 0.5) // 4.0)
                    for i in range(n):
                        u = lo + length / 2 + (i - (n - 1) / 2) * 4.0
                        side.append({"wall": wall, "a": u - 0.8, "b": u + 0.8, "z0": z0, "z1": z1, "open": 1.0})
    return lid, side


# ------------------------------------------------------------------------------------ the heat model
def _h_rad(ts: float, ta: float, eps: float) -> float:
    a, b = ts + 273.15, ta + 273.15
    return eps * SIGMA * (a * a + b * b) * (a + b)


def _h_conv(dt: float, length_m: float, facing: str) -> float:
    """Laminar natural convection from a flat face in still air, W/m²K."""
    dt = max(dt, 0.05)
    k = {"up": 1.32, "side": 1.42, "down": 0.59}[facing]
    return k * (dt / max(length_m, 0.005)) ** 0.25


def _vent_flow(dt: float, ta: float, low: list, high: list, top_z: float) -> float:
    """Air through the openings, m³/s, for inside air dt warmer than the room."""
    if dt <= 0:
        return 0.0
    g = G * dt / (ta + 273.15)
    a_low = sum(o["area"] for o in low)
    a_high = sum(o["area"] for o in high)
    if a_low > 1e-9 and a_high > 1e-9:
        z_low = sum(o["area"] * o["z"] for o in low) / a_low
        hgt = max(0.002, top_z - z_low)
        a_eff = CD / math.sqrt(1 / a_low ** 2 + 1 / a_high ** 2)
        return a_eff * math.sqrt(2 * g * hgt)
    q = 0.0
    for o in low:       # two-way exchange through a vertical opening
        q += CD / 3 * o["w"] * math.sqrt(g) * o["h"] ** 1.5 * o.get("open", 1.0)
    for o in high:      # and through a horizontal slot
        q += 0.055 * o["area"] * math.sqrt(g * o["w"])
    return q


def _gauss(a: list[list[float]], b: list[float]) -> list[float]:
    """Solve a small linear system by elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            if f:
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def solve(box: dict, power_w: float, ambient_c: float, vents: str = "design", material: str | None = None,
          placement: str = "desk") -> dict:
    """Board, inside air and surface temperatures with `power_w` of heat inside the box, in `ambient_c` air."""
    material = material if material in MATERIALS else box["material"]
    k_wall, eps = MATERIALS[material]
    x0, y0, x1, y1 = box["outer"]
    ow, oh, ht = (x1 - x0) / 1000, (y1 - y0) / 1000, (box["z1"] - box["z0"]) / 1000
    w, fl, ld = box["wall"] / 1000, box["floor"] / 1000, box["lid"] / 1000
    iw, ih, iht = ow - 2 * w, oh - 2 * w, max(0.002, ht - fl - ld)
    bw, bh = (v / 1000 for v in box.get("board_mm") or [iw * 1000 * 0.8, ih * 1000 * 0.8])
    a_board = 2 * bw * bh                       # both faces
    if placement == "raised" or box.get("feet"):
        placement = "raised"
    faces = [{"name": "lid", "a": ow * oh, "a_in": iw * ih, "t": ld, "l": ow * oh / (2 * (ow + oh)), "facing": "up"},
             {"name": "sides", "a": 2 * (ow + oh) * ht, "a_in": 2 * (iw + ih) * iht, "t": w, "l": ht, "facing": "side"},
             {"name": "base", "a": ow * oh, "a_in": iw * ih, "t": fl, "l": ow * oh / (2 * (ow + oh)),
              "facing": "desk" if placement == "desk" else "down"}]
    a_in = sum(f["a_in"] for f in faces)
    # heat creeping round the box between the lid and the sides, and the sides and the base
    edge = 2 * (ow + oh)
    g_lat = {(0, 1): k_wall * (ld + w) / 2 * edge / (ht / 2 + min(ow, oh) / 4),
             (1, 2): k_wall * (fl + w) / 2 * edge / (ht / 2 + min(ow, oh) / 4)}
    eps_b = 1 / (1 / BOARD_EMISSIVITY + a_board / a_in * (1 / eps - 1))
    lid_slots, side_slots = vent_slots(box, vents)
    notes = []
    if vents in ("sides", "both") and not side_slots:
        notes.append("There is no room above the board for side vents; make the box taller.")
    ports = [s for s in box["side"] if s.get("name")]
    mm2 = 1e-6
    low = [{"area": (s["b"] - s["a"]) * (s["z1"] - s["z0"]) * s.get("open", 1.0) * mm2,
            "z": (s["z0"] + s["z1"]) / 2000, "w": (s["b"] - s["a"]) / 1000, "h": (s["z1"] - s["z0"]) / 1000,
            "open": s.get("open", 1.0)} for s in side_slots + ports]
    high = [{"area": (r[2] - r[0]) * (r[3] - r[1]) * mm2, "w": min(r[2] - r[0], r[3] - r[1]) / 1000}
            for r in lid_slots + box.get("lid_holes", [])]
    top_z = (box["z1"] - box["lid"]) / 1000

    # nodes: 0 board, 1 air, 2-4 inside of lid/sides/base, 5-7 outside of lid/sides/base; solved with the
    # convection and radiation coefficients from the last guess until they settle
    t = [ambient_c + 10, ambient_c + 5] + [ambient_c + 3] * 3 + [ambient_c + 2] * 3
    for _ in range(60):
        tb, ta = t[0], t[1]
        g = [[0.0] * 8 for _ in range(8)]
        rhs = [0.0] * 8
        rhs[0] = power_w

        def link(i, j, c):
            g[i][i] += c
            g[j][j] += c
            g[i][j] -= c
            g[j][i] -= c

        def to_room(i, c):
            g[i][i] += c
            rhs[i] += c * ambient_c
        l_b = max(min(bw, bh), 0.005)
        h_cb = 0.8 * (1.32 + 0.59) / 2 * (max(tb - ta, 0.05) / l_b) ** 0.25
        link(0, 1, h_cb * a_board)
        for i, f in enumerate(faces):
            wi, wo = 2 + i, 5 + i
            link(0, wi, _h_rad(tb, t[wi], eps_b) * a_board * f["a_in"] / a_in)
            link(1, wi, _h_conv(abs(ta - t[wi]), f["l"] if f["facing"] == "side" else iht, "side") * f["a_in"])
            link(wi, wo, k_wall * (f["a_in"] + f["a"]) / 2 / f["t"])
            to = t[wo]
            h_o = DESK_H if f["facing"] == "desk" else _h_conv(to - ambient_c, f["l"], f["facing"]) + _h_rad(to, ambient_c, eps)
            to_room(wo, h_o * f["a"])
        for (i, j), c in g_lat.items():
            link(5 + i, 5 + j, c)
        q = _vent_flow(ta - ambient_c, ambient_c, low, high, top_z)
        to_room(1, RHO_CP * q)
        new = _gauss(g, rhs)
        done = max(abs(a - b) for a, b in zip(new, t)) < 1e-4
        t = [0.5 * a + 0.5 * b for a, b in zip(new, t)]
        if done:
            break
    tb, ta = t[0], t[1]
    flow = _vent_flow(ta - ambient_c, ambient_c, low, high, top_z)
    q_v = RHO_CP * flow * (ta - ambient_c)
    # what the board sees: its convection to the air and its radiation to the walls, as one temperature
    h_cb = 0.8 * (1.32 + 0.59) / 2 * (max(tb - ta, 0.05) / max(min(bw, bh), 0.005)) ** 0.25
    rads = [_h_rad(tb, t[2 + i], eps_b) * f["a_in"] / a_in for i, f in enumerate(faces)]
    h_b = h_cb + sum(rads)
    env = (h_cb * ta + sum(r * t[2 + i] for i, r in enumerate(rads))) / h_b
    return {"air_c": round(ta, 2), "rise_k": round(ta - ambient_c, 2), "room_c": ambient_c,
            "board_env_c": round(env, 2), "board_c": round(tb, 2), "power_w": round(power_w, 4),
            "surface_c": {f["name"]: round(t[5 + i], 2) for i, f in enumerate(faces)},
            "through_walls_w": round(power_w - q_v, 4), "through_openings_w": round(q_v, 4),
            "flow_l_min": round(flow * 60000, 3),
            "open_mm2": round(sum(o["area"] for o in low + high) / mm2, 1),
            # the plate solver's cooling is per top face, with the underside counted at 0.7
            "board_h": round(h_b * 2 / 1.7, 1),
            "vents": vents, "vents_label": VENT_NAMES[vents], "material": material, "placement": placement,
            "lid_slots": lid_slots, "side_slots": side_slots, "notes": notes}


def total_power_w(state: dict[str, dict]) -> float:
    return sum(s.get("power_mw") or 0 for s in state.values()) / 1000
