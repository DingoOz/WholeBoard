"""Compare the bare-board price at other fabs with the JLCPCB quote.

OSH Park publishes a fixed price per square inch (boards come in sets of three, shipping included), so it is
calculated exactly from their price list. Aisler (Germany) publishes formulas for boards, stencils and assembly
(a job fee plus an area or per-part fee), so it is calculated from those, in euros converted at EUR_USD; their
parts are extra. PCBWay prices each order on their site, and assembly after an engineer reviews the files; only
their well-known promotional price for small 2-layer boards is shown, marked as indicative, with a link to quote
it properly. Prices checked October 2026.
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


EUR_USD = 1.10
# Aisler's Budget service: (job fee, € per cm² per board), minimum 3 boards
AISLER = {2: (12.00, 0.067), 4: (14.00, 0.13)}
AISLER_STENCIL = (5.00, 0.095)
# Aisler assembly: the cheaper of their small-quantity and series price models
AISLER_SMALL = {"setup": 75.00, "two_sided": 30.00, "per_part": 0.38}
AISLER_SERIES = {"setup_per_side": 332.50, "tht": 30.00, "per_part_type": 3.75, "per_part": 0.03}


def placements(jlc_quote: dict) -> tuple[int, int, int, bool]:
    """(parts per board, part types, boards assembled, two-sided) from the JLCPCB quote."""
    asm = jlc_quote.get("assembly") or {}
    lines = (jlc_quote.get("components") or {}).get("lines") or []
    parts = sum(int(l.get("per_board") or len(l["refs"])) for l in lines)
    return parts, len(lines), int(asm.get("boards") or 0), bool(asm.get("bottom_parts"))


def aisler(w_mm: float, h_mm: float, layers: int, qty: int, jlc_quote: dict | None = None) -> dict:
    row = {"fab": "Aisler", "url": "https://aisler.net/", "assembly": True, "kind": "calculated"}
    if layers not in AISLER:
        return {**row, "total": None, "notes": "Aisler's budget service makes 2 and 4 layer boards; quote others on their site."}
    job, per_cm2 = AISLER[layers]
    cm2 = w_mm * h_mm / 100
    boards = max(qty, 3)
    eur = job + cm2 * per_cm2 * boards
    out = {**row, "boards": boards, "total": round(eur * EUR_USD, 2), "per_board": round(eur * EUR_USD / boards, 2),
           "shipping": "extra", "lead_time": "about 7 working days (Budget)",
           "basis": f"€{job:g} + {cm2:.1f} cm² × €{per_cm2:g} × {boards}" + (" (HASL)" if layers == 2 else " (ENIG)"),
           "notes": f"Made in Germany; prices net of VAT, € at ${EUR_USD:g}."}
    parts, types, n, two = placements(jlc_quote or {})
    if parts and n:
        small = AISLER_SMALL["setup"] + (AISLER_SMALL["two_sided"] if two else 0) + AISLER_SMALL["per_part"] * parts * n
        series = AISLER_SERIES["setup_per_side"] * (2 if two else 1) + AISLER_SERIES["per_part_type"] * types \
            + AISLER_SERIES["per_part"] * parts * n
        stencil = (AISLER_STENCIL[0] + cm2 * AISLER_STENCIL[1]) * (2 if two else 1)
        a = min(small, series) + stencil
        out.update(assembly_total=round(a * EUR_USD, 2),
                   assembly_basis=f"{n} boards × {parts} parts, {'small-quantity' if small <= series else 'series'} "
                                  f"pricing, stencil included; parts extra (or €15 per part type you send them)")
    return out


def pcbway_assembly(jlc_quote: dict) -> dict:
    parts, types, n, _ = placements(jlc_quote)
    return {"fab": "PCBWay assembly", "url": "https://www.pcbway.com/pcb-assembly.html", "assembly": True,
            "kind": "quote", "boards": n or None, "total": None,
            "notes": f"{n} boards × {parts} parts of {types} types. PCBWay quotes assembly after an engineer reviews "
                     "the BOM and CPL (1–2 working days); upload the files from Outputs."}


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
            oshpark(w, h, layers, qty), pcbway(w, h, layers, qty), aisler(w, h, layers, qty, jlc_quote)]
    if jlc_quote.get("assembly"):
        rows[0]["assembly_total"] = jlc_quote["assembly"].get("subtotal")
        rows[0]["assembly_basis"] = f"{jlc_quote['assembly'].get('boards')} boards, {jlc_quote['assembly'].get('type')}"
        rows.append(pcbway_assembly(jlc_quote))
    return rows
