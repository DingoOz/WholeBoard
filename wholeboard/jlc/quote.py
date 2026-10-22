"""Build a full JLCPCB quote: bare PCB, assembly, components and shipping."""

from __future__ import annotations

import csv
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import design
from ..kicad import dru, libs
from . import panel, parts as jparts

PRICING_FILE = Path(__file__).with_name("pricing.json")


def pricing() -> dict:
    return json.loads(PRICING_FILE.read_text())


def _line(item: str, amount: float, note: str = "") -> dict:
    return {"item": item, "amount": round(amount, 2), "note": note}


def pcb_price(w: float, h: float, layers: int, qty: int, opts: dict, P: dict) -> tuple[list, float]:
    pc = P["pcb"]
    L = str(layers if layers in (1, 2, 4, 6) else 2)
    lines = []
    promo = pc["promo"][L]
    area_m2 = w * h * qty / 1e6
    if max(w, h) <= max(promo["max_w_mm"], promo["max_h_mm"]) and min(w, h) <= min(
            promo["max_w_mm"], promo["max_h_mm"]) and qty <= promo["max_qty"]:
        base = promo["price"]
        lines.append(_line(f"PCB, {layers} layers, {w:g}×{h:g} mm, qty {qty}", base,
                           "prototype price for boards up to 100×100 mm"))
    else:
        base = max(pc["min_price"][L], pc["engineering_fee"][L] + area_m2 * pc["per_m2"][L])
        lines.append(_line(f"PCB, {layers} layers, {w:g}×{h:g} mm, qty {qty}", base,
                           f"{area_m2:.3f} m² at about ${pc['per_m2'][L]}/m² plus engineering fee"))
    fin = pc["surface_finish"].get(opts.get("surface_finish", "HASL(with lead)"),
                                   {"fixed": 0, "per_m2": 0})
    extra = fin["fixed"] + fin["per_m2"] * area_m2
    if extra:
        lines.append(_line(f"Surface finish: {opts.get('surface_finish')}", extra))
    th = pc["thickness_extra"].get(f"{float(opts.get('thickness_mm', 1.6)):.1f}", 0.0)
    if th:
        lines.append(_line(f"Board thickness {opts.get('thickness_mm')} mm", th))
    sm = pc["solder_mask_extra"].get(opts.get("solder_mask", "Green"), 0.0)
    if sm:
        lines.append(_line(f"Solder mask {opts.get('solder_mask')}", sm))
    return lines, sum(l["amount"] for l in lines)


def bom_rows(spec: dict) -> list[dict]:
    """Group components by LCSC part and footprint, JLCPCB style."""
    spec = design.normalise(spec)
    groups: dict[tuple, dict] = {}
    for c in spec["components"]:
        if c.get("dnp") or str(c.get("footprint", "")).startswith(("MountingHole", "TestPoint", "Fiducial")):
            continue
        fp = libs.load_footprint(c["footprint"])
        smd_pads = tht_pads = 0
        if fp:
            for p in fp.pads:
                if p.kind == "smd":
                    smd_pads += 1
                elif p.kind == "thru_hole":
                    tht_pads += 1
        key = (c.get("lcsc") or "", c.get("value") or "", c["footprint"])
        g = groups.setdefault(key, {"lcsc": c.get("lcsc") or "", "value": c.get("value") or "",
                                    "footprint": c["footprint"], "refs": [],
                                    "smd_joints": smd_pads, "tht_joints": tht_pads,
                                    "description": c.get("description") or ""})
        g["refs"].append(c["ref"])
    return list(groups.values())


def write_bom(rows: list[dict], out: Path) -> Path:
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for r in rows:
            w.writerow([r["value"], ",".join(r["refs"]), r["footprint"].split(":")[-1], r["lcsc"]])
    return out


def full_quote(spec: dict, opts: dict, log=lambda *_: None, geo: dict | None = None) -> dict:
    """opts: quantity, assembly, assembly_quantity, pcba_type, surface_finish, solder_mask,
    shipping_method, thickness_mm. geo (the routed board) adds the features JLCPCB charges extra for."""
    P = pricing()
    spec = design.normalise(spec)
    b = spec["board"]
    w, h, layers = float(b["width_mm"]), float(b["height_mm"]), int(b["layers"])
    qty = int(opts.get("quantity", 5))
    opts = {**opts, "thickness_mm": b.get("thickness_mm", 1.6)}
    assumptions = [
        f"Fab and assembly fees come from WholeBoard's JLCPCB price table (checked {P['last_checked']}). "
        "JLCPCB has no public quote API, so confirm the final price on cart.jlcpcb.com.",
        "Component prices, stock and basic/extended status are live from JLCPCB's parts library.",
    ]
    pan = opts.get("panel") or None
    if pan and int(pan.get("nx", 1)) * int(pan.get("ny", 1)) >= 2:
        lay = panel.layout(w, h, int(pan["nx"]), int(pan["ny"]), float(b.get("corner_radius_mm") or 0) > 0.3)
        n_panels = max(5, math.ceil(qty / lay["boards_per_panel"]))
        pcb_lines, pcb_total = pcb_price(lay["width_mm"], lay["height_mm"], layers, n_panels, opts, P)
        pcb_lines.insert(0, _line(f"Panel by JLCPCB: {lay['nx']} × {lay['ny']} boards, {lay['method']}, "
                                  f"{lay['rail_mm']:g} mm rails = {n_panels * lay['boards_per_panel']} boards", 0.0,
                                  f"{n_panels} panels of {lay['width_mm']:g} × {lay['height_mm']:g} mm"))
        board_qty = n_panels * lay["boards_per_panel"]
        ship_area = lay["width_mm"] * lay["height_mm"] * n_panels
        assumptions.append("Panels are made by JLCPCB from the single-board files (Panel by JLCPCB); choose the "
                           "same columns, rows, rails and separation when ordering.")
    else:
        lay = None
        pcb_lines, pcb_total = pcb_price(w, h, layers, qty, opts, P)
        board_qty = qty
        ship_area = w * h * qty
    features = dru.cost_features(geo, layers) if geo else []
    for f in features:
        if f.get("amount"):
            pcb_lines.append(_line(f["item"], f["amount"], f["note"]))
            pcb_total += f["amount"]
        elif f.get("problem"):
            assumptions.append(f"{f['item']}: {f['note']}")
    result: dict = {
        "currency": "USD", "fab_features": features, "board": {"width_mm": w, "height_mm": h, "layers": layers},
        "quantity": board_qty, "pcb": {"lines": pcb_lines, "subtotal": round(pcb_total, 2)},
        "panel": lay,
        "panel_options": panel.options(spec, opts, lambda pw, ph, n: pcb_price(pw, ph, layers, n, opts, P)[1]),
    }

    rows = bom_rows(spec)
    asm_total = comp_total = 0.0
    # parts on the bottom: where the placer put them, or where the spec says
    if geo and geo.get("footprints"):
        bottom = {f["ref"] for f in geo["footprints"] if f.get("side") == "B.Cu"}
    else:
        bottom = {c["ref"] for c in spec["components"] if design.on_bottom(c)}
    bottom_smd = sorted(r for row in rows if row["lcsc"] and row["smd_joints"] for r in row["refs"] if r in bottom)
    if opts.get("assembly", True) and rows:
        kind = opts.get("pcba_type", "economic")
        A = P["assembly"].get(kind, P["assembly"]["economic"])
        if bottom_smd and not A.get("both_sides"):
            kind = next((k for k, v in P["assembly"].items() if v.get("both_sides")), kind)
            A = P["assembly"][kind]
            assumptions.append(f"{', '.join(bottom_smd[:6])} {'are' if len(bottom_smd) > 1 else 'is'} on the bottom side; "
                               f"JLCPCB assembles both sides only with {kind} assembly, so the quote uses it.")
        n = max(int(opts.get("assembly_quantity", 2)), A["min_boards"])
        n = min(n, board_qty)
        log(f"Pricing {len(rows)} BOM lines at JLCPCB ...")
        codes = sorted({r["lcsc"] for r in rows if r["lcsc"]})
        with ThreadPoolExecutor(max_workers=6) as ex:
            info = dict(zip(codes, ex.map(_safe_lookup, codes)))
        comp_lines = []
        n_ext = n_pref = 0
        smd_j = tht_j = 0
        for r in rows:
            per = len(r["refs"])
            part = info.get(r["lcsc"]) if r["lcsc"] else None
            need = per * n
            line = {"refs": r["refs"], "value": r["value"], "lcsc": r["lcsc"] or None,
                    "footprint": r["footprint"].split(":")[-1], "per_board": per}
            if not part:
                line.update({"status": "missing" if r["lcsc"] else "no LCSC number",
                             "order_qty": 0, "unit": None, "total": 0.0})
                comp_lines.append(line)
                continue
            order = max(need, part["min_order"])
            unit = jparts.unit_price(part, order) or 0.0
            stock_ok = part["stock"] >= order
            line.update({"mpn": part["mpn"], "library_type": part["library_type"],
                         "stock": part["stock"], "order_qty": order, "unit": unit,
                         "total": round(unit * order, 4),
                         "status": "ok" if stock_ok else "short stock"})
            comp_lines.append(line)
            comp_total += unit * order
            if part["library_type"] == "extended":
                n_ext += 1
            elif part["library_type"] == "preferred":
                n_pref += 1
            smd_j += r["smd_joints"] * per
            tht_j += r["tht_joints"] * per
        asm_lines = [_line(f"Assembly setup ({kind})", A["setup"]),
                     _line("Stencil", A["stencil"]),
                     _line(f"SMT joints: {smd_j} per board × {n} boards", smd_j * n * A["per_smd_joint"])]
        if n_ext:
            asm_lines.append(_line(f"Extended part feeder loading: {n_ext} part types",
                                   n_ext * A["extended_part_fee"]))
        if n_pref and A["preferred_part_fee"]:
            asm_lines.append(_line(f"Preferred part loading: {n_pref} part types",
                                   n_pref * A["preferred_part_fee"]))
        if bottom_smd:
            asm_lines.append(_line(f"Bottom-side assembly: {len(bottom_smd)} part{'s' if len(bottom_smd) != 1 else ''}",
                                   A.get("second_side_setup", A["setup"]) + A.get("second_side_stencil", A["stencil"]),
                                   "a second setup and stencil"))
        if tht_j:
            asm_lines.append(_line(f"Through-hole hand soldering: {tht_j} joints × {n} boards",
                                   A["hand_solder_setup"] + tht_j * n * A["per_tht_joint"]))
        asm_total = sum(l["amount"] for l in asm_lines)
        result["assembly"] = {"boards": n, "type": kind, "lines": asm_lines, "bottom_parts": bottom_smd,
                              "subtotal": round(asm_total, 2)}
        result["components"] = {"lines": comp_lines, "subtotal": round(comp_total, 2)}
        if any(l["status"] != "ok" for l in comp_lines):
            assumptions.append("Some parts are missing or short of stock; JLCPCB will not "
                               "assemble those until they are replaced or pre-ordered.")
        assumptions.append("JLCPCB adds attrition (spare parts) for small passives, which can "
                           "raise component cost slightly.")

    # shipping: weight of boards + packaging
    grams = ship_area * float(b.get("thickness_mm", 1.6)) / 1000 * P["board_density_g_cm3"] * 1.15
    grams += P["packaging_g"]
    kg = grams / 1000
    method = opts.get("shipping_method", "Global Standard Direct")
    S = P["shipping"].get(method) or next(iter(P["shipping"].values()))
    ship = S["first_kg"] + max(0, math.ceil(kg - 1)) * S["per_extra_kg"]
    result["shipping"] = {"lines": [_line(f"{method} to {opts.get('shipping_country', '?')}, "
                                          f"about {kg:.2f} kg, {S['days']} days", ship)],
                          "subtotal": round(ship, 2)}
    result["total"] = round(pcb_total + asm_total + comp_total + ship, 2)
    result["per_board"] = round(result["total"] / max(1, result.get("assembly", {}).get("boards", board_qty)), 2)
    result["assumptions"] = assumptions
    result["order_url"] = "https://cart.jlcpcb.com/quote"
    return result


def _safe_lookup(code: str):
    try:
        return jparts.lookup(code)
    except Exception:  # noqa: BLE001
        return None
