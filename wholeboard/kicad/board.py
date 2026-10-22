"""Write a KiCad 10 project (.kicad_pro + .kicad_pcb) from a design spec."""

from __future__ import annotations

import copy
import json
import math
import re
import uuid
from pathlib import Path

from .. import design, impedance, mains, sexpr
from ..sexpr import S, Sym, find, fmt, num, tag
from . import dru, libs, schematic

ORIGIN = (100.0, 100.0)   # board top-left in KiCad coordinates
FORMAT_VERSION = "20260206"

_USER_LAYERS = [
    (9, "F.Adhes", "user", "F.Adhesive"), (11, "B.Adhes", "user", "B.Adhesive"),
    (13, "F.Paste", "user", None), (15, "B.Paste", "user", None),
    (5, "F.SilkS", "user", "F.Silkscreen"), (7, "B.SilkS", "user", "B.Silkscreen"),
    (1, "F.Mask", "user", None), (3, "B.Mask", "user", None),
    (17, "Dwgs.User", "user", "User.Drawings"), (19, "Cmts.User", "user", "User.Comments"),
    (21, "Eco1.User", "user", "User.Eco1"), (23, "Eco2.User", "user", "User.Eco2"),
    (25, "Edge.Cuts", "user", None), (27, "Margin", "user", None),
    (31, "F.CrtYd", "user", "F.Courtyard"), (29, "B.CrtYd", "user", "B.Courtyard"),
    (35, "F.Fab", "user", None), (33, "B.Fab", "user", None),
]


def uid() -> str:
    return str(uuid.uuid4())


def copper_layers(n: int) -> list[str]:
    inner = [f"In{i}.Cu" for i in range(1, max(0, n - 2) + 1)]
    return ["F.Cu", *inner, "B.Cu"] if n > 1 else ["F.Cu"]


def _layers_node(n: int) -> list:
    node = [Sym("layers"), S(0, "F.Cu", Sym("signal"))]
    for i in range(1, max(0, n - 2) + 1):
        node.append(S(2 + 2 * i, f"In{i}.Cu", Sym("signal")))
    if n > 1:
        node.append(S(2, "B.Cu", Sym("signal")))
    for num_, name, kind, alias in _USER_LAYERS:
        item = S(num_, name, Sym(kind))
        if alias:
            item.append(alias)
        node.append(item)
    return node


def _stroke(width: float = 0.05) -> list:
    return S(Sym("stroke"), S(Sym("width"), width), S(Sym("type"), Sym("default")))


def _xy(x: float, y: float) -> list:
    return S(Sym("xy"), x, y)


def outline(w: float, h: float, r: float) -> list[list]:
    x0, y0 = ORIGIN
    x1, y1 = x0 + w, y0 + h
    r = max(0.0, min(r, w / 2 - 0.01, h / 2 - 0.01))
    items: list[list] = []

    def line(a, b):
        items.append([Sym("gr_line"), S(Sym("start"), *a), S(Sym("end"), *b), _stroke(),
                      S(Sym("layer"), "Edge.Cuts"), [Sym("uuid"), uid()]])

    def arc(s, m, e):
        items.append([Sym("gr_arc"), S(Sym("start"), *s), S(Sym("mid"), *m), S(Sym("end"), *e),
                      _stroke(), S(Sym("layer"), "Edge.Cuts"), [Sym("uuid"), uid()]])

    if r <= 0:
        line((x0, y0), (x1, y0)); line((x1, y0), (x1, y1))
        line((x1, y1), (x0, y1)); line((x0, y1), (x0, y0))
        return items
    k = r * (1 - math.sqrt(0.5))
    line((x0 + r, y0), (x1 - r, y0))
    arc((x1 - r, y0), (x1 - k, y0 + k), (x1, y0 + r))
    line((x1, y0 + r), (x1, y1 - r))
    arc((x1, y1 - r), (x1 - k, y1 - k), (x1 - r, y1))
    line((x1 - r, y1), (x0 + r, y1))
    arc((x0 + r, y1), (x0 + k, y1 - k), (x0, y1 - r))
    line((x0, y1 - r), (x0, y0 + r))
    arc((x0, y0 + r), (x0 + k, y0 + k), (x0 + r, y0))
    return items


def polygon_outline(points: list) -> list[list]:
    """A board outline from a polygon (mm from the board's top-left)."""
    x0, y0 = ORIGIN
    pts = [(x0 + float(p[0]), y0 + float(p[1])) for p in points]
    return [[Sym("gr_line"), S(Sym("start"), *a), S(Sym("end"), *b), _stroke(), S(Sym("layer"), "Edge.Cuts"),
             [Sym("uuid"), uid()]] for a, b in zip(pts, pts[1:] + pts[:1])]


def zone(net: str, layer: str, w: float, h: float, inset: float, clearance: float,
         priority: int = 0) -> list:
    x0, y0 = ORIGIN
    pts = [(x0 + inset, y0 + inset), (x0 + w - inset, y0 + inset),
           (x0 + w - inset, y0 + h - inset), (x0 + inset, y0 + h - inset)]
    return [
        Sym("zone"), S(Sym("net"), net), S(Sym("net_name"), net), S(Sym("layer"), layer),
        [Sym("uuid"), uid()], S(Sym("name"), f"{net}_{layer.replace('.', '_')}"),
        S(Sym("hatch"), Sym("edge"), 0.5), S(Sym("priority"), priority),
        # solid joints to SMD pads, thermal reliefs on through-hole pads (hand soldering)
        S(Sym("connect_pads"), Sym("thru_hole_only"), S(Sym("clearance"), clearance)),
        S(Sym("min_thickness"), 0.2), S(Sym("filled_areas_thickness"), Sym("no")),
        S(Sym("fill"), Sym("yes"), S(Sym("thermal_gap"), 0.3), S(Sym("thermal_bridge_width"), 0.4)),
        [Sym("polygon"), [Sym("pts"), *[_xy(*p) for p in pts]]],
    ]


def _rotate_at(node: list, rot: float) -> None:
    at = find(node, "at")
    if not at:
        return
    a = num(at[3]) if len(at) > 3 else 0.0
    new = (a + rot) % 360
    del at[3:]
    if new:
        at.append(fmt(new))


_SIDED = ("Cu", "Adhes", "Paste", "SilkS", "Silkscreen", "Mask", "CrtYd", "Courtyard", "Fab")


def _flip_layer(name: str) -> str:
    if len(name) > 2 and name[:2] in ("F.", "B.") and name[2:] in _SIDED:
        return ("B." if name[0] == "F" else "F.") + name[2:]
    return name


def _mirror(node: list) -> None:
    """Negate the y of every point in a footprint graphic or pad primitive, and swap its layers."""
    for c in node:
        t = tag(c)
        if t in ("start", "end", "mid", "center", "xy") and len(c) >= 3:
            c[2] = fmt(-num(c[2]))
        elif t in ("layer", "layers"):
            for i in range(1, len(c)):
                if isinstance(c[i], str):
                    c[i] = _flip_layer(c[i])
        elif t in ("pts", "arc", "primitives") or (t or "").startswith("gr_"):
            _mirror(c)


def _flip_at(node: list, angle) -> None:
    """(at x y a) -> (at x -y angle(a)), in the footprint's frame."""
    at = find(node, "at")
    if not at or len(at) < 3:
        return
    has = len(at) > 3 and re.fullmatch(r"-?[\d.]+", str(at[3])) is not None
    a = num(at[3]) if has else 0.0
    at[2] = fmt(-num(at[2]))
    new = angle(a) % 360
    if has:
        at[3] = fmt(new)
    elif new:
        at.insert(3, fmt(new))


def _flip_child(child: list) -> None:
    """Put one footprint item on the bottom side: KiCad's top-bottom flip about the footprint origin.

    Local y is negated; pad angles become -a and text angles 180 - a; side-specific layers swap and text on them
    is mirrored. (The same rules as TraceMaker's flip, checked there against pcbnew.)"""
    t = tag(child)
    if t == "pad":
        _flip_at(child, lambda a: -a)
        for q in child:
            qt = tag(q)
            if qt == "layers":
                _mirror([q])
            elif qt == "rect_delta" and len(q) >= 3:
                q[2] = fmt(-num(q[2]))
            elif qt == "chamfer":
                for i in range(1, len(q)):
                    v = str(q[i])
                    q[i] = Sym("bottom_" + v[4:]) if v.startswith("top_") else Sym("top_" + v[7:]) if v.startswith("bottom_") else q[i]
            elif qt == "drill":
                off = find(q, "offset")
                if off and len(off) >= 3:
                    off[2] = fmt(-num(off[2]))
            elif qt == "primitives":
                _mirror(q)
    elif t in ("property", "fp_text"):
        lay = find(child, "layer")
        if not lay:
            return
        _flip_at(child, lambda a: 180 - a)
        if _flip_layer(str(lay[1])) != str(lay[1]):
            eff = find(child, "effects")
            if eff is None:
                child.append([Sym("effects"), S(Sym("justify"), Sym("mirror"))])
            else:
                j = find(eff, "justify")
                if j is None:
                    eff.append(S(Sym("justify"), Sym("mirror")))
                elif "mirror" in [str(x) for x in j[1:]]:
                    j[:] = [x for x in j if str(x) != "mirror"]
                else:
                    j.append(Sym("mirror"))
            lay[1] = _flip_layer(str(lay[1]))
    elif t in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly", "fp_curve"):
        _mirror(child)


MIN_DRILL_MM = 0.3      # JLCPCB's standard-process smallest hole, as min_through_hole_diameter below


def _min_drill(pad: list) -> None:
    """Widen holes smaller than the fab's minimum (thermal vias under a module's ground pad are often 0.2 mm) to
    0.3 mm, keeping at least a 0.1 mm copper ring."""
    for n in pad:
        if tag(n) == "drill" and len(n) == 2 and not isinstance(n[1], list) and 0 < num(n[1]) < MIN_DRILL_MM:
            n[1] = fmt(MIN_DRILL_MM)
            for s in pad:
                if tag(s) == "size" and len(s) == 3:
                    s[1], s[2] = fmt(max(num(s[1]), MIN_DRILL_MM + 0.2)), fmt(max(num(s[2]), MIN_DRILL_MM + 0.2))


def footprint_instance(fp: libs.Footprint, ref: str, value: str, x: float, y: float,
                       rot: float = 0.0, locked: bool = False, pad_nets: dict | None = None,
                       extra_props: dict | None = None, sheet_link: tuple[str, str, str] | None = None,
                       bottom: bool = False) -> list:
    """A board footprint built from a library footprint, on the top side or flipped onto the bottom."""
    pad_nets = pad_nets or {}
    src = copy.deepcopy(fp.node)
    out: list = [Sym("footprint"), fp.lib_id]
    if locked:
        out.append(S(Sym("locked"), Sym("yes")))
    out += [S(Sym("layer"), "B.Cu" if bottom else "F.Cu"), [Sym("uuid"), uid()], S(Sym("at"), x, y, *([rot] if rot else []))]
    skip = {"version", "generator", "generator_version", "layer", "uuid", "at", "locked", "placed"}
    have_props = set()
    for child in src[2:]:
        t = tag(child)
        if t in skip or t is None:
            continue
        for n in sexpr.walk(child):
            if tag(n) == "uuid" and len(n) > 1:
                n[1] = uid()
        if bottom:
            _flip_child(child)
        if t == "property" and len(child) > 2:
            have_props.add(child[1])
            if child[1] == "Reference":
                child[2] = ref
            elif child[1] == "Value":
                child[2] = value
            if rot:
                _rotate_at(child, rot)
        elif t in ("fp_text", "pad") and rot:
            _rotate_at(child, rot)
        if t == "pad":
            name = child[1] if len(child) > 1 and isinstance(child[1], str) else ""
            _min_drill(child)
            for i in range(len(child) - 1, -1, -1):
                if tag(child[i]) == "net":
                    del child[i]
            net = pad_nets.get(str(name))
            if net:
                child.insert(len(child) - 1 if tag(child[-1]) == "uuid" else len(child),
                             S(Sym("net"), net))
        out.append(child)
    if sheet_link:
        path, sheetname, sheetfile = sheet_link
        out += [S(Sym("path"), path), S(Sym("sheetname"), sheetname), S(Sym("sheetfile"), sheetfile)]
    for key, val in (extra_props or {}).items():
        if not val:
            continue
        if key in have_props:
            for child in out:
                if tag(child) == "property" and child[1] == key:
                    child[2] = str(val)
            continue
        out.insert(5, [Sym("property"), key, str(val), S(Sym("at"), 0, 0, 0),
                       S(Sym("layer"), "B.Fab" if bottom else "F.Fab"), S(Sym("hide"), Sym("yes")), [Sym("uuid"), uid()],
                       [Sym("effects"), [Sym("font"), S(Sym("size"), 1, 1), S(Sym("thickness"), 0.15)]]])
    return out


def build(spec: dict, out_dir: Path) -> dict:
    """Write <name>.kicad_pcb and <name>.kicad_pro. Returns paths and placement notes."""
    spec = design.normalise(spec)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = spec["name"]
    b = spec["board"]
    rules = spec["rules"]
    w, h = float(b["width_mm"]), float(b["height_mm"])
    nlayers = int(b["layers"])
    x0, y0 = ORIGIN
    pin_net = design.pin_net_map(spec)

    pcb: list = [
        Sym("kicad_pcb"), S(Sym("version"), Sym(FORMAT_VERSION)), S(Sym("generator"), "pcbnew"),
        S(Sym("generator_version"), "10.0"),
        [Sym("general"), S(Sym("thickness"), float(b["thickness_mm"])), S(Sym("legacy_teardrops"), Sym("no"))],
        S(Sym("paper"), "A4"),
        _layers_node(nlayers),
        [Sym("setup"), S(Sym("pad_to_mask_clearance"), 0),
         S(Sym("allow_soldermask_bridges_in_footprints"), Sym("no"))],
    ]

    # Parts without a fixed place start in a pile beside the board, for the placer.
    pile_x, pile_y, row_h = x0 + w + 10, y0, 0.0
    links = schematic.board_links(spec)
    placed_notes = []
    for c in spec["components"]:
        fp = libs.load_footprint(c["footprint"])
        if fp is None:
            raise ValueError(f"footprint {c['footprint']} not found")
        ref = c["ref"]
        pad_nets = {p.split(".", 1)[1]: n for p, n in pin_net.items() if p.split(".", 1)[0] == ref}
        cx0, cy0, cx1, cy1 = fp.courtyard
        pl = c.get("placement") or {}
        if "x_mm" in pl and "y_mm" in pl:
            x, y = x0 + float(pl["x_mm"]), y0 + float(pl["y_mm"])
            rot = float(pl.get("rotation") or 0)
            locked = bool(pl.get("locked", True))
            placed_notes.append(f"{ref} fixed at ({pl['x_mm']}, {pl['y_mm']}) mm")
        else:
            fw, fh = cx1 - cx0, cy1 - cy0
            if pile_x + fw > x0 + w + 10 + max(60.0, w):
                pile_x, pile_y, row_h = x0 + w + 10, pile_y + row_h + 2, 0.0
            x, y = pile_x - cx0, pile_y - cy0
            pile_x += fw + 2
            row_h = max(row_h, fh)
            rot, locked = 0.0, False
        props = {"LCSC": c.get("lcsc"), "MPN": c.get("mpn"), "Description": c.get("description")}
        link = links.get(ref)
        pcb.append(footprint_instance(fp, ref, str(c.get("value") or ref), x, y, rot, locked,
                                      pad_nets, props, link, bottom=design.on_bottom(c)))

    mh = b.get("mounting_holes")
    if mh:
        fp = libs.load_footprint(design.hole_footprint(spec)) or libs.load_footprint("MountingHole:MountingHole_3.2mm_M3")
        for i, (mx, my) in enumerate(design.hole_positions(spec), start=1):
            pcb.append(footprint_instance(fp, f"H{i}", "MountingHole", x0 + mx, y0 + my, 0, True))

    if b.get("outline"):
        pcb += polygon_outline(b["outline"])
    else:
        pcb += outline(w, h, float(b.get("corner_radius_mm") or 0))

    pcb += fixed_copper(spec)

    for layer, net in (b.get("planes") or {}).items():
        if layer in copper_layers(nlayers):
            pcb.append(zone(net, layer, w, h, rules["edge_clearance_mm"], rules["clearance_mm"]))

    pcb.append(S(Sym("embedded_fonts"), Sym("no")))
    pcb_path = out_dir / f"{name}.kicad_pcb"
    pcb_path.write_text(sexpr.dumps(pcb) + "\n", encoding="utf-8")
    pro_path = out_dir / f"{name}.kicad_pro"
    pro_path.write_text(json.dumps(project_json(spec), indent=2), encoding="utf-8")
    dru.write(spec, out_dir)
    return {"pcb": str(pcb_path), "pro": str(pro_path), "notes": placed_notes}


def fixed_copper(spec: dict) -> list[list]:
    """Tracks and vias the router must keep (hand-routed in KiCad), locked."""
    x0, y0 = ORIGIN
    layers = set(copper_layers(int(spec["board"]["layers"])))
    out = []
    for cu in spec.get("fixed_copper") or []:
        net = cu["net"]
        for t in cu.get("tracks") or []:
            if t["layer"] in layers:
                out.append([Sym("segment"), S(Sym("start"), x0 + t["a"][0], y0 + t["a"][1]),
                            S(Sym("end"), x0 + t["b"][0], y0 + t["b"][1]), S(Sym("width"), float(t["w"])),
                            S(Sym("locked"), Sym("yes")), S(Sym("layer"), t["layer"]), S(Sym("net"), net), [Sym("uuid"), uid()]])
        for t in cu.get("arcs") or []:
            if t["layer"] in layers:
                out.append([Sym("arc"), S(Sym("start"), x0 + t["a"][0], y0 + t["a"][1]),
                            S(Sym("mid"), x0 + t["m"][0], y0 + t["m"][1]), S(Sym("end"), x0 + t["b"][0], y0 + t["b"][1]),
                            S(Sym("width"), float(t["w"])), S(Sym("locked"), Sym("yes")), S(Sym("layer"), t["layer"]),
                            S(Sym("net"), net), [Sym("uuid"), uid()]])
        for v in cu.get("vias") or []:
            out.append([Sym("via"), S(Sym("locked"), Sym("yes")), S(Sym("at"), x0 + v["p"][0], y0 + v["p"][1]),
                        S(Sym("size"), float(v["d"])), S(Sym("drill"), float(v["drill"])),
                        S(Sym("layers"), "F.Cu", "B.Cu"), S(Sym("net"), net), [Sym("uuid"), uid()]])
    return out


def add_ground_pour(pcb_path: Path, spec: dict) -> bool:
    """Add a ground pour on the outer layers (after routing). Returns False if not wanted."""
    spec = design.normalise(spec)
    net = spec["board"].get("ground_pour")
    names = {n["name"] for n in spec["nets"]}
    if not net or net not in names:
        return False
    b, rules = spec["board"], spec["rules"]
    tree = sexpr.parse(pcb_path.read_text(encoding="utf-8"))
    tree = [n for n in tree if not (tag(n) == "zone" and find(n, "name")
                                    and str(find(n, "name")[1]).startswith(f"{net}_") and
                                    str(find(n, "layer")[1]) in ("F.Cu", "B.Cu"))]
    insert_at = next((i for i, n in enumerate(tree) if tag(n) == "embedded_fonts"), len(tree))
    layers = ["F.Cu", "B.Cu"] if int(b["layers"]) > 1 else ["F.Cu"]
    for layer in layers:
        tree.insert(insert_at, zone(net, layer, float(b["width_mm"]), float(b["height_mm"]),
                                    rules["edge_clearance_mm"], rules["clearance_mm"]))
    pcb_path.write_text(sexpr.dumps(tree) + "\n", encoding="utf-8")
    return True


def project_json(spec: dict) -> dict:
    r = spec["rules"]
    power = [n["name"] for n in spec["nets"] if n.get("class") == "power"]
    controlled = impedance.netclasses(spec)
    hv = mains.netclasses(spec)
    imp_nets = {n for k in controlled for n in k["nets"]} | {n for v in hv.values() for n in v}

    def netclass(name: str, width: float, prio: int, dp_width: float = 0.2, dp_gap: float = 0.25) -> dict:
        return {
            "name": name, "clearance": r["clearance_mm"], "track_width": width,
            "via_diameter": r["via_diameter_mm"], "via_drill": r["via_drill_mm"],
            "microvia_diameter": 0.3, "microvia_drill": 0.1,
            "diff_pair_width": dp_width, "diff_pair_gap": dp_gap, "diff_pair_via_gap": 0.25,
            "bus_width": 12, "wire_width": 6, "line_style": 0, "priority": prio,
            "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
            "tuning_profile": "",
        }

    return {
        "board": {
            "design_settings": {
                "rules": {
                    # JLCPCB standard-process capabilities, with some margin.
                    "min_clearance": 0.127, "min_track_width": 0.127,
                    "min_via_diameter": 0.45, "min_via_annular_width": 0.1,
                    "min_through_hole_diameter": MIN_DRILL_MM, "min_hole_to_hole": 0.25,
                    "min_hole_clearance": 0.2, "min_copper_edge_clearance": r["edge_clearance_mm"],
                    "min_silk_clearance": 0.0, "min_text_height": 0.8, "min_text_thickness": 0.08,
                    "min_microvia_diameter": 0.2, "min_microvia_drill": 0.1,
                    "min_connection": 0.0, "min_groove_width": 0.0, "min_resolved_spokes": 1,
                    "solder_mask_to_copper_clearance": 0.0, "max_error": 0.005,
                    "use_height_for_length_calcs": True,
                },
                "track_widths": [0.0, r["track_mm"], r["power_track_mm"]],
                "via_dimensions": [{"diameter": 0.0, "drill": 0.0},
                                   {"diameter": r["via_diameter_mm"], "drill": r["via_drill_mm"]}],
            },
        },
        "meta": {"filename": f"{spec['name']}.kicad_pro", "version": 3},
        "net_settings": {
            "classes": [netclass("Default", r["track_mm"], 2147483647),
                        netclass("Power", r["power_track_mm"], 0)]
            + [netclass(k["name"], k["width"] or r["track_mm"], 1 + i, k["width"] or r["track_mm"],
                        k["gap"] or max(r["clearance_mm"], 0.15)) for i, k in enumerate(controlled)]
            + [netclass(name, r["power_track_mm"], len(controlled) + 1 + i) for i, (name, nets) in enumerate(hv.items()) if nets],
            "meta": {"version": 5},
            "net_colors": None,
            "netclass_assignments": None,
            "netclass_patterns": [{"netclass": "Power", "pattern": n} for n in power if n not in imp_nets]
            + [{"netclass": k["name"], "pattern": n} for k in controlled for n in k["nets"]]
            + [{"netclass": name, "pattern": n} for name, nets in hv.items() for n in nets],
        },
        "pcbnew": {"page_layout_descr_file": ""},
        "sheets": [], "text_variables": {},
    }
