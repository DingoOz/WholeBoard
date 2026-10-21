"""Read a board outline from a DXF drawing: the largest closed shape is the board, circles are holes.

Handles LINE, ARC, CIRCLE, LWPOLYLINE (with bulges) and POLYLINE/VERTEX, in millimetres or inches
($INSUNITS). Coordinates come back in WholeBoard's convention: mm from the board's top-left, y down.
"""

from __future__ import annotations

import math
import re

SEG_MM = 0.5          # arcs become straight segments about this long


def _pairs(text: str):
    lines = text.splitlines()
    for i in range(0, len(lines) - 1, 2):
        yield lines[i].strip(), lines[i + 1].strip()


def _entities(text: str) -> tuple[list[dict], float]:
    m = re.search(r"\$INSUNITS\s*\n\s*70\s*\n\s*(\d+)", text)
    units = int(m[1]) if m else None
    scale = 1.0
    ents: list[dict] = []
    section, cur = None, None
    for code, val in _pairs(text):
        if code == "0":
            if cur is not None:
                ents.append(cur)
                cur = None
            if val == "SECTION":
                section = "?"
            elif val == "ENDSEC":
                section = None
            elif section == "ENTITIES" and val in ("LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "VERTEX", "SEQEND"):
                cur = {"type": val, "pts": [], "bulges": []}
            continue
        if code == "2" and section == "?":
            section = val
            continue
        if cur is None:
            continue
        try:
            f = float(val)
        except ValueError:
            continue
        if code == "10":
            cur["pts"].append([f, 0.0])
            cur["bulges"].append(0.0)
        elif code == "20" and cur["pts"]:
            cur["pts"][-1][1] = f
        elif code == "11":
            cur["p2"] = [f, 0.0]
        elif code == "21" and "p2" in cur:
            cur["p2"][1] = f
        elif code == "40":
            cur["r"] = f
        elif code == "50":
            cur["a0"] = f
        elif code == "51":
            cur["a1"] = f
        elif code == "42" and cur["bulges"]:
            cur["bulges"][-1] = f
        elif code == "70":
            cur["flags"] = int(f)
    if cur is not None:
        ents.append(cur)
    if units == 1:
        scale = 25.4
    elif units == 5:
        scale = 10.0
    elif units == 6:
        scale = 1000.0
    return ents, scale


def _arc(cx, cy, r, a0, a1) -> list[list[float]]:
    while a1 <= a0:
        a1 += 360
    n = max(4, int(math.radians(a1 - a0) * r / SEG_MM))
    return [[cx + r * math.cos(math.radians(a0 + (a1 - a0) * k / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * k / n))] for k in range(n + 1)]


def _bulge(p, q, b) -> list[list[float]]:
    """Points from p to q along a polyline bulge (tan of a quarter of the arc's angle)."""
    if abs(b) < 1e-9:
        return [p, q]
    ang = 4 * math.atan(b)
    d = math.dist(p, q)
    r = d / (2 * math.sin(abs(ang) / 2))
    mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
    h = math.sqrt(max(r * r - (d / 2) ** 2, 0))
    ux, uy = (q[0] - p[0]) / d, (q[1] - p[1]) / d
    s = 1 if (b > 0) == (abs(ang) < math.pi) else -1
    cx, cy = mx - s * uy * h, my + s * ux * h
    a0 = math.degrees(math.atan2(p[1] - cy, p[0] - cx))
    a1 = math.degrees(math.atan2(q[1] - cy, q[0] - cx))
    pts = _arc(cx, cy, r, a0, a1) if b > 0 else _arc(cx, cy, r, a1, a0)[::-1]
    pts[0], pts[-1] = list(p), list(q)
    return pts


def _chains(segs: list[list[list[float]]], tol: float = 0.05) -> list[list[list[float]]]:
    """Join open pieces end to end into closed loops."""
    loops = []
    pool = [s for s in segs if len(s) >= 2]
    while pool:
        chain = pool.pop(0)
        grown = True
        while grown and math.dist(chain[0], chain[-1]) > tol:
            grown = False
            for i, s in enumerate(pool):
                if math.dist(chain[-1], s[0]) <= tol:
                    chain += s[1:]
                elif math.dist(chain[-1], s[-1]) <= tol:
                    chain += s[::-1][1:]
                elif math.dist(chain[0], s[-1]) <= tol:
                    chain = s[:-1] + chain
                elif math.dist(chain[0], s[0]) <= tol:
                    chain = s[::-1][:-1] + chain
                else:
                    continue
                pool.pop(i)
                grown = True
                break
        if math.dist(chain[0], chain[-1]) <= tol and len(chain) >= 4:
            loops.append(chain[:-1])
    return loops


def area(poly: list[list[float]]) -> float:
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))) / 2


def _simplify(poly: list[list[float]], tol: float = 0.01) -> list[list[float]]:
    out = []
    for i, p in enumerate(poly):
        a, b = poly[i - 1], poly[(i + 1) % len(poly)]
        cross = (p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])
        if abs(cross) > tol * max(math.dist(a, b), 1e-9):
            out.append(p)
    return out


def read(text: str) -> dict:
    """{"outline": [[x, y], ...], "width_mm", "height_mm", "holes": [{"x_mm", "y_mm", "diameter_mm"}], "notes"}."""
    ents, scale = _entities(text)
    segs, circles, notes = [], [], []
    verts: list[dict] = []
    for e in ents:
        t, pts = e["type"], e["pts"]
        if t == "LINE" and pts and "p2" in e:
            segs.append([pts[0], e["p2"]])
        elif t == "ARC" and pts and "r" in e:
            segs.append(_arc(pts[0][0], pts[0][1], e["r"], e.get("a0", 0), e.get("a1", 360)))
        elif t == "CIRCLE" and pts and "r" in e:
            circles.append((pts[0][0], pts[0][1], e["r"]))
        elif t == "LWPOLYLINE" and len(pts) >= 2:
            closed = bool(e.get("flags", 0) & 1)
            path = [pts[0]]
            n = len(pts) if closed else len(pts) - 1
            for i in range(n):
                path += _bulge(pts[i], pts[(i + 1) % len(pts)], e["bulges"][i])[1:]
            segs.append(path)
        elif t == "VERTEX" and pts:
            verts.append({"p": pts[0], "b": e["bulges"][0] if e["bulges"] else 0.0})
        elif t == "SEQEND" and verts:
            path = [verts[0]["p"]]
            for a, b in zip(verts, verts[1:] + verts[:1]):
                path += _bulge(a["p"], b["p"], a["b"])[1:]
            segs.append(path)
            verts = []
    loops = _chains(segs)
    if not loops and circles:
        cx, cy, r = max(circles, key=lambda c: c[2])
        circles.remove((cx, cy, r))
        loops = [_arc(cx, cy, r, 0, 360)[:-1]]
    if not loops:
        raise ValueError("No closed outline found in the DXF (lines, arcs and polylines must join up).")
    board = max(loops, key=area)
    if len(loops) > 1:
        notes.append(f"{len(loops) - 1} smaller closed shapes were ignored (cut-outs are not supported yet).")
    xs, ys = [p[0] for p in board], [p[1] for p in board]
    x0, y1 = min(xs), max(ys)
    tf = lambda x, y: [round((x - x0) * scale, 3), round((y1 - y) * scale, 3)]  # noqa: E731
    outline = _simplify([tf(*p) for p in board])
    w, h = round((max(xs) - x0) * scale, 3), round((y1 - min(ys)) * scale, 3)
    holes = []
    for cx, cy, r in circles:
        if min(xs) < cx < max(xs) and min(ys) < cy < max(ys):
            x, y = tf(cx, cy)
            holes.append({"x_mm": x, "y_mm": y, "diameter_mm": round(2 * r * scale, 2)})
    if scale != 1.0:
        notes.append(f"Converted from {'inches' if scale == 25.4 else 'drawing units'} to millimetres.")
    rect = len(outline) == 4 and abs(area(outline) - w * h) < 0.01 * w * h
    return {"outline": None if rect else outline, "width_mm": w, "height_mm": h, "holes": holes,
            "rectangular": rect, "notes": notes}
