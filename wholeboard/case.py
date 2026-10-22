"""Fit the board into a box you already have, and print drilling templates for its walls and lid.

The design names the case in board.case, using the figures from the box maker's drawing:

  "case": {"name": "Hammond 1591XXSSBK", "inner_mm": [80, 50, 26],    // inside length (x), width (y), height
           "board_at_mm": [2, 2],          // board's top-left from the inside top-left corner (default: centred)
           "standoff_mm": 5,               // board underside above the inside floor (the bosses' height)
           "wall_mm": 2.5, "floor_mm": 2.5,
           "material": "ABS"}              // ABS, PC, aluminium or "aluminium (painted)": for the heat estimate

Put the mounting holes over the box's bosses with board.mounting_holes.positions (board coordinates), or import
the box maker's DXF of the PCB outline. WholeBoard checks that the board and its tallest part fit, then draws a
1:1 template for each wall that needs openings (connectors at the edges) and for the lid (LEDs, buttons).
"""

from __future__ import annotations

import html
from pathlib import Path

from . import enclosure

MARGIN = 0.5       # clearance around each opening, mm
PAGE = 12          # paper margin around a template, mm
PORT_REACH = 4.0   # a connector this close to an edge gets a wall opening


def layout(spec: dict, ctx: dict) -> dict:
    case = spec["board"]["case"]
    L, W, H = (float(v) for v in case["inner_mm"])
    bw, bh = ctx["board"]["width_mm"], ctx["board"]["height_mm"]
    at = case.get("board_at_mm") or [(L - bw) / 2, (W - bh) / 2]
    return {"name": case.get("name") or "case", "L": L, "W": W, "H": H, "ax": float(at[0]), "ay": float(at[1]),
            "standoff": float(case.get("standoff_mm", 5)), "wall": float(case.get("wall_mm", 2)),
            "floor": float(case.get("floor_mm", case.get("wall_mm", 2))), "thick": ctx["board"]["thickness_mm"],
            "bw": bw, "bh": bh}


def check(spec: dict, ctx: dict) -> tuple[list[str], list[str]]:
    c = layout(spec, ctx)
    problems, warnings = [], []
    if c["ax"] < 0 or c["ay"] < 0 or c["ax"] + c["bw"] > c["L"] + 0.01 or c["ay"] + c["bh"] > c["W"] + 0.01:
        problems.append(f"The {c['bw']:g} × {c['bh']:g} mm board at ({c['ax']:g}, {c['ay']:g}) does not fit the "
                        f"{c['L']:g} × {c['W']:g} mm inside of the {c['name']}")
    top = [p for p in ctx["parts"] if p["side"] == "top"]
    bot = [p for p in ctx["parts"] if p["side"] == "bottom"]
    z_top = c["standoff"] + c["thick"]
    if top:
        tall = max(top, key=lambda p: p["height_mm"])
        room = c["H"] - z_top
        if tall["height_mm"] > room - 0.5:
            problems.append(f"{tall['ref']} stands {tall['height_mm']:g} mm tall but there is only {room:.1f} mm "
                            f"between the board and the lid")
        elif tall["height_mm"] > room - 2:
            warnings.append(f"{tall['ref']} clears the lid by only {room - tall['height_mm']:.1f} mm")
    if bot:
        tall = max(bot, key=lambda p: p["height_mm"])
        if tall["height_mm"] > c["standoff"] - 0.5:
            problems.append(f"{tall['ref']} under the board is {tall['height_mm']:g} mm tall, more than the "
                            f"{c['standoff']:g} mm standoff")
    for p in ctx["parts"]:
        if p.get("height_estimated") and p["kind"] in ("port", "user"):
            warnings.append(f"{p['ref']}'s height is an estimate; check its opening against the datasheet")
    return problems, warnings


def openings(spec: dict, ctx: dict) -> dict:
    """Wall cut-outs and lid holes, each in its face's coordinates (mm from the face's bottom-left, outside view)."""
    c = layout(spec, ctx)
    z_top = c["standoff"] + c["thick"]
    faces: dict[str, list] = {"front": [], "back": [], "left": [], "right": [], "lid": []}
    for p in ctx["parts"]:
        x0, y0, x1, y1 = p["courtyard_mm"]
        X0, X1, Y0, Y1 = c["ax"] + x0, c["ax"] + x1, c["ay"] + y0, c["ay"] + y1     # inside coordinates, y down
        if p["kind"] == "port" and p["distance_to_edge_mm"] <= PORT_REACH:
            z0, z1 = (z_top, z_top + p["height_mm"]) if p["side"] == "top" else (c["standoff"] - p["height_mm"], c["standoff"])
            wall = p["nearest_wall"]
            if wall == "top":       # board's top edge: seen from outside, the board's left is on the right
                u0, u1, face = c["L"] - X1, c["L"] - X0, "back"
            elif wall == "bottom":
                u0, u1, face = X0, X1, "front"
            elif wall == "left":
                u0, u1, face = Y0, Y1, "left"
            else:
                u0, u1, face = c["W"] - Y1, c["W"] - Y0, "right"
            faces[face].append({"ref": p["ref"], "value": p.get("value"), "shape": "rect",
                                "u": round(u0 - MARGIN, 2), "v": round(z0 - MARGIN, 2),
                                "w": round(u1 - u0 + 2 * MARGIN, 2), "h": round(z1 - z0 + 2 * MARGIN, 2)})
        elif p["side"] == "top" and p["kind"] in ("led", "user"):
            cx, cy = (X0 + X1) / 2, (Y0 + Y1) / 2
            d = float(p["_extra"].get("hole", 3.0 if p["kind"] == "led" else 4.0)) if "_extra" in p else 3.0
            faces["lid"].append({"ref": p["ref"], "value": p.get("value"), "shape": "circle",
                                 "u": round(cx, 2), "v": round(c["W"] - cy, 2), "d": d})
    return {"case": c, "faces": faces}


FACE_TITLES = {"front": "Front wall (the board's bottom edge)", "back": "Back wall (the board's top edge)",
               "left": "Left wall", "right": "Right wall", "lid": "Lid, seen from above"}


def _svg(face: str, c: dict, cuts: list) -> str:
    wall, floor = c["wall"], c["floor"]
    if face == "lid":
        w, h, ox, oy = c["L"] + 2 * wall, c["W"] + 2 * wall, wall, wall
    else:
        span = c["L"] if face in ("front", "back") else c["W"]
        w, h, ox, oy = span + 2 * wall, c["H"] + floor, wall, floor
    W, Hh = w + 2 * PAGE, h + 2 * PAGE + 14
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.1f}mm" height="{Hh:.1f}mm" viewBox="0 0 {W:.2f} {Hh:.2f}" '
           f'font-family="sans-serif">',
           f'<rect x="{PAGE}" y="{PAGE}" width="{w:.2f}" height="{h:.2f}" fill="none" stroke="#000" stroke-width="0.3"/>',
           f'<text x="{PAGE}" y="{PAGE - 3}" font-size="3.5">{html.escape(FACE_TITLES[face])} — {html.escape(c["name"])}'
           f' (outside {w:g} × {h:g} mm; line up the outline with the box)</text>']
    if face != "lid":
        out.append(f'<line x1="{PAGE}" y1="{PAGE + h - floor:.2f}" x2="{PAGE + w:.2f}" y2="{PAGE + h - floor:.2f}" '
                   f'stroke="#888" stroke-width="0.2" stroke-dasharray="1 1"/>')
        out.append(f'<text x="{PAGE + 1}" y="{PAGE + h - floor - 1:.2f}" font-size="2.2" fill="#888">inside floor</text>')

    def P(u, v):    # face coordinates (from the inside bottom-left) to page coordinates
        return PAGE + ox + u, PAGE + h - oy - v

    for k in cuts:
        if k["shape"] == "circle":
            x, y = P(k["u"], k["v"])
            r = k["d"] / 2
            out.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" fill="#f2c" fill-opacity="0.25" stroke="#c0a" stroke-width="0.3"/>')
            out.append(f'<path d="M{x - r - 1.5:.2f},{y:.2f}H{x + r + 1.5:.2f}M{x:.2f},{y - r - 1.5:.2f}V{y + r + 1.5:.2f}" stroke="#000" stroke-width="0.15"/>')
            out.append(f'<text x="{x + r + 1:.2f}" y="{y - r - 0.5:.2f}" font-size="2.4">{html.escape(k["ref"])} ⌀{k["d"]:g}</text>')
        else:
            x, y = P(k["u"], k["v"] + k["h"])
            out.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{k["w"]:.2f}" height="{k["h"]:.2f}" fill="#f2c" fill-opacity="0.25" '
                       f'stroke="#c0a" stroke-width="0.3"/>')
            out.append(f'<text x="{x:.2f}" y="{y - 1:.2f}" font-size="2.4">{html.escape(k["ref"])} {k["w"]:g}×{k["h"]:g}</text>')
    # a 50 mm bar to check the print scale
    bx, by = PAGE, PAGE + h + 8
    out.append(f'<path d="M{bx},{by}H{bx + 50}M{bx},{by - 1.5}V{by + 1.5}M{bx + 50},{by - 1.5}V{by + 1.5}" stroke="#000" stroke-width="0.3"/>')
    out.append(f'<text x="{bx + 52}" y="{by + 1}" font-size="2.6">50 mm: print at 100 % (no "fit to page")</text>')
    out.append("</svg>")
    return "\n".join(out)


def build(spec: dict, geo: dict, out_dir: Path, name: str, part_heights: dict | None = None) -> dict:
    ctx = enclosure.context(geo, spec, part_heights)
    problems, warnings = check(spec, ctx)
    o = openings(spec, ctx)
    c, faces = o["case"], o["faces"]
    out_dir.mkdir(parents=True, exist_ok=True)
    files, rows = [], []
    sections = []
    for face, cuts in faces.items():
        if not cuts:
            continue
        svg = _svg(face, c, cuts)
        f = out_dir / f"{name}-{face}-template.svg"
        f.write_text(svg)
        files.append({"face": face, "title": FACE_TITLES[face], "path": f})
        sections.append(f'<section><h2>{html.escape(FACE_TITLES[face])}</h2>{svg}</section>')
        for k in cuts:
            rows.append({"face": face, **k})
    page = out_dir / f"{name}-drilling-templates.html"
    page.write_text("<!doctype html><html><head><meta charset='utf-8'><title>Drilling templates</title><style>"
                    "body{font:13px sans-serif;margin:10mm;color:#000;background:#fff}section{page-break-after:always;"
                    "margin-bottom:12mm}h2{font-size:15px}@media print{button{display:none}}</style></head><body>"
                    f"<h1>{html.escape(name)} in a {html.escape(c['name'])}</h1>"
                    "<p>Print at 100 % and check the 50 mm bar. Tape each template to the outside of its face, "
                    "centre-punch the cross-hairs, drill the round holes and cut the rectangles with a step drill "
                    "and a file.</p><button onclick='print()'>Print</button>" + "".join(sections) + "</body></html>")
    return {"ok": not problems, "case": {k: c[k] for k in ("name", "L", "W", "H", "ax", "ay", "standoff")},
            "problems": problems, "warnings": warnings, "cutouts": rows, "files": files, "page": page,
            "headroom_mm": round(c["H"] - c["standoff"] - c["thick"] -
                                 max([p["height_mm"] for p in ctx["parts"] if p["side"] == "top"] or [0]), 2)}
