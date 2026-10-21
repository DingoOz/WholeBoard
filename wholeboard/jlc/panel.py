"""Panels for small boards: price several copies on one panel against single boards.

WholeBoard does not draw the panel. JLCPCB's "Panel by JLCPCB" option builds it from the single
board's Gerbers, so the recommendation is the settings to choose there: columns × rows, edge rails,
and V-cut (straight edges) or tab routing with mouse bites (rounded corners or odd outlines).
"""
from __future__ import annotations

import math

MAX_PANEL_MM = 250.0
PROMO_MM = 100.0


def layout(w: float, h: float, nx: int, ny: int, rounded: bool, rail_mm: float = 5.0) -> dict:
    gap = 2.0 if rounded else 0.0       # tab routing needs a milled gap; V-cut boards touch
    pw = nx * w + (nx - 1) * gap
    ph = ny * h + (ny - 1) * gap + 2 * rail_mm
    return {"nx": nx, "ny": ny, "boards_per_panel": nx * ny, "width_mm": round(pw, 2), "height_mm": round(ph, 2),
            "method": "tabs (mouse bites)" if rounded else "V-cut", "gap_mm": gap, "rail_mm": rail_mm}


def options(spec: dict, opts: dict, price_fn) -> dict:
    """price_fn(w, h, qty) -> PCB fabrication cost. Returns layouts ranked by cost per board."""
    b = spec["board"]
    w, h = float(b["width_mm"]), float(b["height_mm"])
    want = int(opts.get("quantity", 5))
    rounded = float(b.get("corner_radius_mm") or 0) > 0.3
    single = price_fn(w, h, want)
    rows = []
    for nx in range(1, 9):
        for ny in range(1, 9):
            if nx * ny < 2:
                continue
            lay = layout(w, h, nx, ny, rounded)
            if max(lay["width_mm"], lay["height_mm"]) > MAX_PANEL_MM:
                continue
            panels = max(5, math.ceil(want / (nx * ny)))
            cost = price_fn(lay["width_mm"], lay["height_mm"], panels)
            boards = panels * nx * ny
            rows.append({**lay, "panels": panels, "boards": boards, "pcb_cost": round(cost, 2),
                         "per_board": round(cost / boards, 3),
                         "promo_size": max(lay["width_mm"], lay["height_mm"]) <= PROMO_MM})
    # Layouts that make at least the boards wanted without many spares, cheapest first.
    rows = [r for r in rows if want <= r["boards"] <= max(4 * want, 50)]
    rows.sort(key=lambda r: (r["pcb_cost"], r["boards"], abs(r["nx"] - r["ny"])))
    best = rows[0] if rows else None
    small = min(w, h) < 20 or w * h < 600
    out = {"single": {"boards": want, "pcb_cost": round(single, 2), "per_board": round(single / max(want, 1), 3)},
           "layouts": rows[:6], "recommended": None, "small_board": small,
           "how": "In JLCPCB's order form choose Panel by JLCPCB, set the columns and rows, add 5 mm edge rails "
                  "on the long sides, and pick V-cut for straight-edged boards or tab routing for rounded ones."}
    # Recommend a panel when the board is small (easier to assemble on rails) or it is clearly cheaper
    # for the boards wanted: what the singles cost against what enough panels cost.
    if best:
        cheapest = best
        if small or cheapest["pcb_cost"] < single * 0.85:
            out["recommended"] = cheapest
            out["reason"] = ("Small boards are easier to assemble and handle on a panel with edge rails."
                             if small and cheapest["pcb_cost"] >= single * 0.85 else
                             f"{cheapest['boards']} boards on {cheapest['panels']} panels cost "
                             f"${cheapest['pcb_cost']:.2f} against ${single:.2f} for {want} single boards.")
    return out
