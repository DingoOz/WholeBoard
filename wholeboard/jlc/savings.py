"""Cheaper parts: basic or preferred JLCPCB parts that can replace extended ones, with the saving.

Only swaps that cannot change the circuit are offered: the same resistor, capacitor or LED value in
the same package (with an equal or better voltage rating and tolerance), or the same manufacturer
part number listed under a basic/preferred LCSC number. Parts that are missing or short of stock
get an in-stock replacement even if it costs more.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from . import parts as jparts
from .quote import pricing

PACKAGE_RE = re.compile(r"^(?:R|C|L|LED|D)_(\d{4})_")
COLOURS = ("red", "green", "blue", "yellow", "white", "orange", "amber", "pink", "purple")
RANK = {"basic": 0, "preferred": 1, "extended": 2}


def _volts(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*V\b", text or "")
    return float(m.group(1)) if m else None


def _tolerance(text: str) -> float | None:
    m = re.search(r"±\s*(\d+(?:\.\d+)?)\s*%", text or "")
    return float(m.group(1)) if m else None


def _passive(line: dict, comp: dict) -> tuple[str, str, str] | None:
    """(kind, value, package) when this BOM line is a plain resistor, capacitor or LED."""
    m = PACKAGE_RE.match(line["footprint"])
    if not m:
        return None
    package, ref = m.group(1), line["refs"][0]
    value = (comp.get("value") or line.get("value") or "").split()[0] if (comp.get("value") or line.get("value")) else ""
    if line["footprint"].startswith("LED_"):
        text = " ".join(str(comp.get(k) or "") for k in ("value", "description", "mpn")).lower()
        colour = next((c for c in COLOURS if c in text), None)
        return ("led", colour, package) if colour else None
    if ref.startswith("R") and not ref.startswith("RV") and jparts.parse_value(value) is not None:
        return "resistor", value, package
    if ref.startswith("C") and jparts.parse_value(value) is not None:
        return "capacitor", value, package
    return None


def _fits(old: dict | None, new: dict, kind: str, comp: dict) -> bool:
    """The replacement is at least as good: voltage rating and tolerance not worse."""
    old_text = " ".join(str(x or "") for x in ((old or {}).get("description"), comp.get("value"), comp.get("description")))
    new_text = new.get("description") or ""
    if kind == "capacitor":
        need, have = _volts(old_text), _volts(new_text)
        if have is None or have < (need or 10):
            return False
    if kind == "resistor":
        need, have = _tolerance(old_text), _tolerance(new_text)
        if need is not None and (have is None or have > need):
            return False
    return True


def _candidates(line: dict, comp: dict, old: dict | None) -> list[dict]:
    p = _passive(line, comp)
    out = []
    if p:
        kind, value, package = p
        out = [c for c in jparts.find_passive(kind, value, package, limit=8) if _fits(old, c, kind, comp)]
    mpn = (old or {}).get("mpn") or comp.get("mpn")
    if mpn:
        out += [c for c in jparts.search(mpn, limit=8) if (c.get("mpn") or "").lower() == mpn.lower()]
    return out


def find(quote: dict, spec: dict, log=lambda *_: None) -> dict:
    """Swaps for the quote's component lines, cheapest first, and the total they save."""
    comps = {c["ref"]: c for c in spec.get("components") or []}
    lines = (quote.get("components") or {}).get("lines") or []
    boards = (quote.get("assembly") or {}).get("boards") or 0
    if not lines or not boards:
        return {"suggestions": [], "total_saving": 0.0}
    asm = pricing()["assembly"].get((quote.get("assembly") or {}).get("type") or "economic") or {}
    fee = {"extended": asm.get("extended_part_fee", 3.0), "preferred": asm.get("preferred_part_fee", 0.0)}
    todo = [l for l in lines if l.get("library_type") in ("extended", "preferred") and fee.get(l["library_type"])
            or l.get("status") not in ("ok", None) or not l.get("lcsc")]
    log(f"Looking for cheaper or in-stock replacements for {len(todo)} part{'s' if len(todo) != 1 else ''}")

    def best(line: dict) -> dict | None:
        comp = comps.get(line["refs"][0], {})
        old = jparts.lookup(line["lcsc"]) if line.get("lcsc") else None
        need = line["per_board"] * boards
        old_cost = (line.get("total") or 0) + fee.get(line.get("library_type") or "", 0)
        broken = line.get("status") != "ok"
        picks = []
        for c in _candidates(line, comp, old):
            if c["lcsc"] == line.get("lcsc") or c["stock"] < need:
                continue
            order = max(need, c.get("min_order") or 1)
            unit = jparts.unit_price(c, order) or 0.0
            cost = unit * order + fee.get(c["library_type"], 0)
            saving = old_cost - cost if not broken else 0.0
            if broken or (saving > 0.005 and RANK.get(c["library_type"], 3) <= RANK.get(line.get("library_type"), 3)):
                picks.append((not broken and -saving, cost, c, order, unit, saving))
        if not picks:
            return None
        picks.sort(key=lambda t: (t[0], t[1]))
        _, cost, c, order, unit, saving = picks[0]
        reason = ("replaces a part JLCPCB cannot supply" if broken else
                  f"{line.get('library_type')} → {c['library_type']}" + (", no feeder fee" if fee.get(line.get("library_type") or "")
                                                                         and not fee.get(c["library_type"]) else ""))
        return {"refs": line["refs"], "value": line.get("value"), "footprint": line["footprint"],
                "from": {"lcsc": line.get("lcsc"), "mpn": line.get("mpn"), "library_type": line.get("library_type"),
                         "status": line.get("status"), "cost": round(old_cost, 4)},
                "to": {"lcsc": c["lcsc"], "mpn": c.get("mpn"), "library_type": c["library_type"],
                       "description": c.get("description"), "stock": c["stock"], "unit": unit, "order_qty": order,
                       "cost": round(cost, 4)},
                "saving": round(saving, 2), "reason": reason}

    with ThreadPoolExecutor(4) as ex:
        found = [s for s in ex.map(lambda l: _safe(best, l), todo) if s]
    found.sort(key=lambda s: -s["saving"])
    return {"suggestions": found, "total_saving": round(sum(s["saving"] for s in found), 2)}


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001 - a failed search just means no suggestion for that line
        return None


def apply(spec: dict, swaps: list[dict]) -> list[str]:
    """Point each swap's components at the new LCSC number. Returns the refs changed."""
    by_ref = {c["ref"]: c for c in spec.get("components") or []}
    changed = []
    for s in swaps:
        code = str(s.get("lcsc") or "").upper()
        if not re.fullmatch(r"C\d+", code):
            continue
        for r in s.get("refs") or []:
            c = by_ref.get(r)
            if c is not None and c.get("lcsc") != code:
                c["lcsc"] = code
                c.pop("mpn", None)
                changed.append(r)
    return changed
