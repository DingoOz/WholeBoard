"""Live stock check before ordering: JLCPCB's stock moves daily, so check again right before you order."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from . import parts as jparts, savings
from .quote import bom_rows, pricing


def _fresh(code: str):
    try:
        return jparts.lookup(code, fresh=True)
    except Exception:  # noqa: BLE001 - unreachable counts as unknown, not as out of stock
        return "error"


def check(spec: dict, opts: dict, log=lambda *_: None) -> dict:
    """Every BOM line against JLCPCB's live stock for the boards being assembled."""
    asm = pricing()["assembly"].get(opts.get("pcba_type") or "economic") or {}
    boards = max(int(opts.get("assembly_quantity") or 2), int(asm.get("min_boards") or 2)) if opts.get("assembly", True) \
        else int(opts.get("quantity") or 5)
    rows = bom_rows(spec)
    codes = sorted({r["lcsc"] for r in rows if r["lcsc"]})
    log(f"Checking live JLCPCB stock for {len(codes)} parts")
    with ThreadPoolExecutor(max_workers=6) as ex:
        info = dict(zip(codes, ex.map(_fresh, codes)))
    lines, to_replace = [], []
    for r in rows:
        per = len(r["refs"])
        need = per * boards
        line = {"refs": r["refs"], "value": r["value"], "lcsc": r["lcsc"] or None,
                "footprint": r["footprint"].split(":")[-1], "per_board": per, "need": need}
        part = info.get(r["lcsc"]) if r["lcsc"] else None
        if not r["lcsc"]:
            line.update(status="no LCSC number", note="JLCPCB cannot assemble it; fit it by hand or add an LCSC part")
        elif part == "error":
            line.update(status="unknown", note="JLCPCB did not answer; try again")
        elif not part:
            line.update(status="not found", note="JLCPCB does not list this part number")
        else:
            order = max(need, part.get("min_order") or 1)
            line.update(mpn=part.get("mpn"), library_type=part.get("library_type"), stock=part["stock"],
                        order_qty=order, unit=jparts.unit_price(part, order))
            if not part.get("buyable", True):
                line.update(status="unavailable", note=part.get("no_buy_reason") or "JLCPCB no longer sells it")
            elif part["stock"] < order:
                line.update(status="short", note=f"only {part['stock']} in stock, {order} needed"
                            + (f"; restock expected {part['restock_date']}" if part.get("restock_date") else ""))
            elif part["stock"] < max(5 * order, 100):
                line.update(status="low", note=f"{part['stock']} left: it could sell out before your order is placed")
            else:
                line["status"] = "ok"
            if part.get("alternative"):
                line["jlc_alternative"] = part["alternative"]
            if line["status"] == "ok" and part.get("library_type") == "extended":
                line["note"] = f"extended part: ${asm.get('extended_part_fee', 3.0):g} feeder fee"
        lines.append(line)
        if line["status"] in ("short", "unavailable", "not found", "low"):
            to_replace.append({**line, "status": "short stock" if line["status"] != "low" else "low",
                               "total": (line.get("unit") or 0) * (line.get("order_qty") or 0)})
    replacements = []
    if to_replace:
        log(f"Looking for in-stock replacements for {len(to_replace)} parts")
        fake = {"components": {"lines": [{**l, "status": "short stock"} for l in to_replace]},
                "assembly": {"boards": boards, "type": opts.get("pcba_type") or "economic"}}
        try:
            replacements = savings.find(fake, spec, log)["suggestions"]
        except Exception as e:  # noqa: BLE001
            log(f"Replacement search failed: {e}")
    bad = [l for l in lines if l["status"] in ("short", "unavailable", "not found")]
    return {"checked_at": time.strftime("%Y-%m-%d %H:%M"), "boards": boards, "lines": lines,
            "ok": not bad, "problems": len(bad), "low": sum(l["status"] == "low" for l in lines),
            "extended": sum(l.get("library_type") == "extended" for l in lines),
            "replacements": replacements}
