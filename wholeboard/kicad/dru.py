"""A KiCad custom-rules file (.kicad_dru) with JLCPCB's manufacturing limits.

The project's design settings only hold KiCad's basic minimums. The rules file adds the ones JLCPCB states for
the board's layer count: annular rings, hole spacing, hole to copper, silkscreen to pads, silkscreen text size,
copper to the board edge and the drill aspect ratio. The values live in jlc/capabilities.json.

Also finds the features JLCPCB charges extra for (small vias, vias in pads), for the quote.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

CAPS_FILE = Path(__file__).resolve().parent.parent / "jlc" / "capabilities.json"


def capabilities() -> dict:
    return json.loads(CAPS_FILE.read_text())


def limits(layers: int, thickness_mm: float = 1.6) -> dict:
    """JLCPCB's limits for a board, merged into one dict."""
    C = capabilities()
    key = "2" if layers <= 2 else ("4" if layers <= 4 else "6")
    out = {**C["any"], **C["layers"][key]}
    # a drill must be at least the board thickness over the aspect ratio
    out["min_drill_aspect"] = round(float(thickness_mm) / out["max_aspect_ratio"], 3)
    out["min_via_drill"] = max(out["min_via_drill"], out["min_drill_aspect"])
    return out


def _mm(v: float) -> str:
    return f"{v:g}mm"


def text(layers: int, thickness_mm: float = 1.6, edge_clearance_mm: float | None = None) -> str:
    """The .kicad_dru file's contents."""
    L = limits(layers, thickness_mm)
    edge = max(L["edge_clearance"], edge_clearance_mm or 0)
    rules = [
        ("Track width", None, f"(constraint track_width (min {_mm(L['min_track'])}))", None),
        ("Copper clearance", "A.Type != 'Zone' && B.Type != 'Zone'",
         f"(constraint clearance (min {_mm(L['min_clearance'])}))", None),
        ("Via drill", "A.Type == 'Via'", f"(constraint hole_size (min {_mm(L['min_via_drill'])}))", None),
        ("Via diameter", "A.Type == 'Via'", f"(constraint via_diameter (min {_mm(L['min_via_diameter'])}))", None),
        ("Via annular ring", "A.Type == 'Via'", f"(constraint annular_width (min {_mm(L['via_annular'])}))", None),
        ("Pad annular ring", "A.Type == 'Pad' && A.Pad_Type == 'Through-hole'",
         f"(constraint annular_width (min {_mm(L['pad_annular'])}))", None),
        ("Plated hole size", "A.Type == 'Pad' && A.Pad_Type == 'Through-hole'",
         f"(constraint hole_size (min {_mm(max(L['min_pth_drill'], L['min_drill_aspect']))}))", None),
        ("Hole to hole", "!(A.Type == 'Via' && B.Type == 'Via') && A.Net != B.Net",
         f"(constraint hole_to_hole (min {_mm(L['hole_to_hole'])}))", None),
        ("Via hole to via hole", "A.Type == 'Via' && B.Type == 'Via'",
         f"(constraint hole_to_hole (min {_mm(L['via_to_via_hole'])}))", None),
        ("Hole to copper", None, f"(constraint hole_clearance (min {_mm(L['hole_to_copper'])}))", None),
        ("Copper to board edge", None, f"(constraint edge_clearance (min {_mm(edge)}))", None),
        ("Silkscreen to pads", "A.Type == 'Pad' && (B.Type == 'Text' || B.Type == 'Graphic')",
         f"(constraint silk_clearance (min {_mm(L['silk_to_pad'])}))", "outer"),
        ("Silkscreen text height", "A.Type == 'Text' || A.Type == 'Text Box'",
         f"(constraint text_height (min {_mm(L['min_text_height'])}))", '"?.Silkscreen"'),
        ("Silkscreen text thickness", "A.Type == 'Text' || A.Type == 'Text Box'",
         f"(constraint text_thickness (min {_mm(L['min_text_thickness'])}))", '"?.Silkscreen"'),
    ]
    lines = [f"# JLCPCB manufacturing limits for a {layers}-layer, {thickness_mm:g} mm board.",
             "# Written by WholeBoard from wholeboard/jlc/capabilities.json; edit that file, not this one.",
             "(version 1)"]
    for name, cond, constraint, layer in rules:
        lines.append(f'(rule "JLCPCB: {name}"')
        if layer:
            lines.append(f"  (layer {layer})")
        if cond:
            lines.append(f'  (condition "{cond}")')
        lines.append(f"  {constraint})")
    return "\n".join(lines) + "\n"


def write(spec: dict, out_dir: Path) -> Path:
    b = spec["board"]
    path = out_dir / f"{spec['name']}.kicad_dru"
    from .. import mains
    body = text(int(b["layers"]), float(b.get("thickness_mm") or 1.6), (spec.get("rules") or {}).get("edge_clearance_mm"))
    extra = mains.dru_rules(spec)
    path.write_text(body + ("\n".join(extra) + "\n" if extra else ""), encoding="utf-8")
    return path


def _in_pad(v: dict, pads: list[dict]) -> dict | None:
    for p in pads:
        if p.get("kind") != "smd":
            continue
        # the pad's box, turned with the pad; a via whose hole overlaps it is in the pad
        a = math.radians(-float(p.get("rot") or 0))
        dx, dy = v["p"][0] - p["x"], v["p"][1] - p["y"]
        lx, ly = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
        r = float(v.get("drill") or 0.3) / 2
        if abs(lx) < p["w"] / 2 + r and abs(ly) < p["h"] / 2 + r:
            return p
    return None


def cost_features(geo: dict, layers: int) -> list[dict]:
    """The board's features that JLCPCB charges extra for (or cannot make): small vias and vias in pads."""
    C = capabilities()["cost"]
    vias = geo.get("vias") or []
    out = []
    sv = C["small_via"]
    small = [v for v in vias if float(v.get("drill") or 1) < sv["below_drill_mm"] - 1e-6]
    if small and layers in sv["layers"]:
        out.append({"feature": "small_via", "count": len(small), "amount": sv["fixed"],
                    "item": f"Small vias: {len(small)} under {sv['below_drill_mm']:g} mm drill", "note": sv["note"]})
    elif small:
        out.append({"feature": "small_via", "count": len(small), "amount": None, "problem": True,
                    "item": f"Small vias: {len(small)} under {sv['below_drill_mm']:g} mm drill",
                    "note": f"JLCPCB does not make vias this small on {layers}-layer boards."})
    vp = C["via_in_pad"]
    hits = []
    for v in vias:
        p = _in_pad(v, geo.get("pads") or [])
        if p:
            hits.append(f"{p['ref']}.{p['name']}")
    if hits:
        fee = vp["layers"].get(str(layers))
        out.append({"feature": "via_in_pad", "count": len(hits), "pads": hits[:10], "amount": fee,
                    "problem": fee is None,
                    "item": f"Vias in pads: {len(hits)} ({', '.join(hits[:4])}{'…' if len(hits) > 4 else ''})",
                    "note": vp["note"]})
    return out
