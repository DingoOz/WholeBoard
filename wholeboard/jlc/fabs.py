"""Compare the bare-board price at other fabs with the JLCPCB quote.

OSH Park publishes a fixed price per square inch (boards come in sets of three, shipping included), so it is
calculated exactly from their price list. PCBWay prices each order on their site; only their well-known
promotional price for small 2-layer boards is shown, marked as indicative, with a link to quote it properly.
Prices checked October 2026.
"""

from __future__ import annotations

import math

OSHPARK = {2: 5.0, 4: 10.0, 6: 15.0}     # US$ per square inch for three copies
OSHPARK_DAYS = {2: "9–12 days", 4: "9–14 days", 6: "12–21 days"}


def oshpark(w_mm: float, h_mm: float, layers: int, qty: int) -> dict:
    row = {"fab": "OSH Park", "url": "https://oshpark.com/", "assembly": False, "kind": "calculated",
           "notes": "ENIG finish and purple solder mask only; no assembly. Super Swift (4–5 business days) costs twice as "
                    "much on 2 and 4 layers."}
    if layers not in OSHPARK:
        return {**row, "total": None, "notes": "OSH Park makes 2, 4 and 6 layer boards only."}
    sq_in = (w_mm / 25.4) * (h_mm / 25.4)
    sets = max(1, math.ceil(qty / 3))
    total = round(sq_in * OSHPARK[layers] * sets, 2)
    return {**row, "boards": sets * 3, "total": total, "per_board": round(total / (sets * 3), 2),
            "shipping": "included", "lead_time": OSHPARK_DAYS[layers],
            "basis": f"{sq_in:.2f} in² × ${OSHPARK[layers]:g} per set of 3 × {sets} set{'s' if sets > 1 else ''}"}


def pcbway(w_mm: float, h_mm: float, layers: int, qty: int) -> dict:
    row = {"fab": "PCBWay", "url": "https://www.pcbway.com/orderonline.aspx", "assembly": True, "kind": "indicative"}
    if layers <= 2 and w_mm <= 100 and h_mm <= 100 and qty <= 10:
        return {**row, "boards": max(qty, 5), "total": 5.0, "per_board": round(5.0 / max(qty, 5), 2),
                "shipping": "extra (about $20–30 by courier)", "lead_time": "2–4 days + shipping",
                "notes": "Their promotional price for small 2-layer boards; repeat orders and other options cost more. "
                         "Quote it on their site."}
    return {**row, "boards": qty, "total": None, "notes": "Bigger, multilayer or larger runs are priced per order: get a quote "
                                                          "on their site."}


def compare(spec: dict, jlc_quote: dict) -> list[dict]:
    b = spec["board"]
    w, h, layers = float(b["width_mm"]), float(b["height_mm"]), int(b["layers"])
    qty = int(jlc_quote.get("quantity") or 5)
    pcb = (jlc_quote.get("pcb") or {}).get("subtotal")
    ship = (jlc_quote.get("shipping") or {}).get("subtotal")
    rows = [{"fab": "JLCPCB", "url": "https://cart.jlcpcb.com/quote", "assembly": True, "kind": "live",
             "boards": qty, "total": round((pcb or 0) + (ship or 0), 2) if pcb is not None else None,
             "per_board": round(((pcb or 0) + (ship or 0)) / qty, 2) if pcb is not None and qty else None,
             "shipping": f"${ship:.2f}" if ship is not None else "–", "lead_time": "2–3 days + shipping",
             "notes": "Bare boards with shipping, from the quote above (assembly and parts not included here)."},
            oshpark(w, h, layers, qty), pcbway(w, h, layers, qty)]
    return rows
