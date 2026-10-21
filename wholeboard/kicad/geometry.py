"""Read a .kicad_pcb into simple JSON geometry for the browser board view."""

from __future__ import annotations

import math
from pathlib import Path

from .. import sexpr
from ..sexpr import find, find_all, num, tag


def _rot(x: float, y: float, deg: float) -> tuple[float, float]:
    if not deg:
        return x, y
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return x * c + y * s, -x * s + y * c


def _pt(node) -> list[float]:
    return [round(num(node[1]), 4), round(num(node[2]), 4)]


def read(pcb: Path) -> dict:
    tree = sexpr.parse(pcb.read_text(encoding="utf-8"))
    out: dict = {"outline": [], "footprints": [], "pads": [], "tracks": [], "arcs": [],
                 "vias": [], "zones": [], "layers": []}
    layers = find(tree, "layers")
    if layers:
        out["layers"] = [str(l[1]) for l in layers[1:] if isinstance(l, list) and str(l[1]).endswith(".Cu")]
    for node in tree[1:]:
        t = tag(node)
        layer = find(node, "layer") if isinstance(node, list) else None
        lname = str(layer[1]) if layer else ""
        if t in ("gr_line", "gr_arc", "gr_rect", "gr_circle") and lname == "Edge.Cuts":
            item = {"kind": t[3:]}
            for key in ("start", "mid", "end", "center"):
                k = find(node, key)
                if k:
                    item[key] = _pt(k)
            out["outline"].append(item)
        elif t == "segment":
            out["tracks"].append({"a": _pt(find(node, "start")), "b": _pt(find(node, "end")),
                                  "w": num(find(node, "width")[1]), "layer": lname,
                                  "net": _net(node)})
        elif t == "arc":
            out["arcs"].append({"a": _pt(find(node, "start")), "m": _pt(find(node, "mid")),
                                "b": _pt(find(node, "end")), "w": num(find(node, "width")[1]),
                                "layer": lname, "net": _net(node)})
        elif t == "via":
            out["vias"].append({"p": _pt(find(node, "at")), "d": num(find(node, "size")[1]),
                                "drill": num(find(node, "drill")[1]), "net": _net(node)})
        elif t == "zone":
            net = _net(node)
            for fp_ in find_all(node, "filled_polygon"):
                fl = find(fp_, "layer")
                pts = find(fp_, "pts")
                if pts:
                    out["zones"].append({"layer": str(fl[1]) if fl else lname, "net": net,
                                         "pts": [_pt(p) for p in pts[1:] if tag(p) == "xy"]})
        elif t == "footprint":
            _footprint(node, out)
    xs, ys = [], []
    for o in out["outline"]:
        for k in ("start", "end", "mid"):
            if k in o:
                xs.append(o[k][0]); ys.append(o[k][1])
    if xs:
        out["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
    out["ratsnest"] = ratsnest(out)
    return out


def _net(node) -> str:
    n = find(node, "net")
    if not n or len(n) < 2:
        return ""
    v = n[-1]
    return str(v) if isinstance(v, str) else ""


def _footprint(node: list, out: dict) -> None:
    at = find(node, "at")
    fx, fy = num(at[1]), num(at[2])
    frot = num(at[3]) if len(at) > 3 else 0.0
    ref = ""
    for p in find_all(node, "property"):
        if p[1] == "Reference":
            ref = str(p[2])
    xs, ys = [], []
    for g in node:
        if tag(g) in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
            ly = find(g, "layer")
            if ly and str(ly[1]) in ("F.CrtYd", "B.CrtYd"):
                for pt in sexpr.walk(g):
                    if tag(pt) in ("start", "end", "xy", "mid"):
                        dx, dy = _rot(num(pt[1]), num(pt[2]), frot)
                        xs.append(fx + dx); ys.append(fy + dy)
    side = str(find(node, "layer")[1])
    fpid = {"ref": ref, "lib": str(node[1]), "x": fx, "y": fy, "rot": frot, "side": side}
    models = []
    for m in find_all(node, "model"):
        def xyz(name, d):
            n = find(m, name)
            v = find(n, "xyz") if n else None
            return [num(v[1]), num(v[2]), num(v[3])] if v else d
        hide = find(m, "hide")
        models.append({"path": str(m[1]), "offset": xyz("offset", [0, 0, 0]), "scale": xyz("scale", [1, 1, 1]),
                       "rotate": xyz("rotate", [0, 0, 0]),
                       "hide": "hide" in [str(x) for x in m] or bool(hide and str(hide[1]) == "yes")})
    if models:
        fpid["models"] = models
    if xs:
        fpid["court"] = [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]
    out["footprints"].append(fpid)
    for p in find_all(node, "pad"):
        pat = find(p, "at")
        px, py = num(pat[1]), num(pat[2])
        pang = num(pat[3]) if len(pat) > 3 else 0.0
        dx, dy = _rot(px, py, frot)
        size = find(p, "size")
        lay = find(p, "layers")
        lays = [str(x) for x in lay[1:]] if lay else []
        drill = find(p, "drill")
        out["pads"].append({
            "ref": ref, "name": str(p[1]), "x": round(fx + dx, 4), "y": round(fy + dy, 4),
            "w": num(size[1]) if size else 0, "h": num(size[2]) if size else 0,
            "rot": pang, "shape": str(p[3]), "kind": str(p[2]),
            "drill": num(drill[1]) if drill and len(drill) > 1 and not isinstance(drill[1], str) else 0,
            "front": any(l in ("F.Cu", "*.Cu") for l in lays),
            "back": any(l in ("B.Cu", "*.Cu") for l in lays),
            "net": _net(p),
        })


def ratsnest(geo: dict) -> list[list[list[float]]]:
    """Air wires: a minimum spanning tree per net over its pads (ignores copper)."""
    by_net: dict[str, list[tuple[float, float]]] = {}
    for p in geo["pads"]:
        if p["net"]:
            by_net.setdefault(p["net"], []).append((p["x"], p["y"]))
    lines = []
    for pts in by_net.values():
        if len(pts) < 2 or len(pts) > 400:
            continue
        inside = {0}
        best = {i: (math.dist(pts[0], pts[i]), 0) for i in range(1, len(pts))}
        while best:
            i = min(best, key=lambda k: best[k][0])
            _, j = best.pop(i)
            lines.append([list(pts[j]), list(pts[i])])
            inside.add(i)
            for k in best:
                d = math.dist(pts[i], pts[k])
                if d < best[k][0]:
                    best[k] = (d, i)
    return lines
