"""Build a WholeBoard enclosure in FreeCAD.

WholeBoard runs this with FreeCAD's command-line mode (freecadcmd) and passes the resolved geometry
in the JSON file named by $WB_ENCLOSURE_JOB. The same file is saved as a macro with the job embedded
(<name>-enclosure.FCMacro), so you can open it in FreeCAD, change numbers and run it again.

Coordinates are millimetres: X right, Y towards the board's top edge, Z up from the bottom of the base.
"""

import json
import math
import os
import traceback

import FreeCAD as App
import Part

V = App.Vector
NOTES = []
REPORT = {}       # extra results for the report (where the label went)


def say(msg):
    print("WB: " + msg, flush=True)


def edges_along(shape, axis):
    """Straight edges parallel to an axis (0 = X, 1 = Y, 2 = Z)."""
    out = []
    for e in shape.Edges:
        a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
        d = b - a
        if d.Length < 1e-6:
            continue
        if abs(abs(d[axis]) / d.Length - 1) < 1e-6:
            out.append(e)
    return out


def rbox(x, y, z, w, d, h, r=0.0, axis=2):
    """A box with the edges parallel to `axis` rounded by r."""
    b = Part.makeBox(w, d, h, V(x, y, z))
    dims = (w, d, h)
    r = min(r, min(dims[i] for i in range(3) if i != axis) / 2 - 0.02)
    if r > 0.05:
        try:
            b = b.makeFillet(r, edges_along(b, axis))
        except Exception as ex:  # noqa: BLE001
            NOTES.append("A corner could not be rounded (%s); it was left square." % ex)
    return b


def cyl(x, y, z, d, h, axis=2):
    direction = (V(1, 0, 0), V(0, 1, 0), V(0, 0, 1))[axis]
    return Part.makeCylinder(d / 2.0, h, V(x, y, z), direction)


def fuse_all(shape, others):
    others = [o for o in others if o is not None]
    if not others:
        return shape
    return shape.fuse(others).removeSplitter()


def cut_all(shape, others):
    others = [o for o in others if o is not None]
    if not others:
        return shape
    return shape.cut(Part.makeCompound(others))


def wall_cut(job, op):
    """A rounded or round opening through one side wall."""
    ow, oh = job["outer"]
    t = job["wall"] + 2.0
    u0, u1, z0, z1 = op["u0"], op["u1"], op["z0"], op["z1"]
    span, height = u1 - u0, z1 - z0
    if span <= 0.2 or height <= 0.2:
        NOTES.append("Opening for %s is too small to cut." % op.get("name"))
        return None
    if op["wall"] in ("left", "right"):
        x = -1.0 if op["wall"] == "left" else ow - job["wall"] - 1.0
        if op["shape"] == "round" and abs(span - height) < 0.5:
            return cyl(x, (u0 + u1) / 2, (z0 + z1) / 2, min(span, height), t, axis=0)
        r = min(span, height) / 2 - 0.02 if op["shape"] == "round" else min(1.2, min(span, height) / 2 - 0.02)
        return rbox(x, u0, z0, t, span, height, r, axis=0)
    y = -1.0 if op["wall"] == "bottom" else oh - job["wall"] - 1.0
    if op["shape"] == "round" and abs(span - height) < 0.5:
        return cyl((u0 + u1) / 2, y, (z0 + z1) / 2, min(span, height), t, axis=1)
    r = min(span, height) / 2 - 0.02 if op["shape"] == "round" else min(1.2, min(span, height) / 2 - 0.02)
    return rbox(u0, y, z0, span, t, height, r, axis=1)


def build_base(job):
    ow, oh = job["outer"]
    cx, cy, cw, ch = job["cavity"]
    floor, base_h = job["floor"], job["base_h"]
    say("Building the base")
    base = rbox(0, 0, 0, ow, oh, base_h, job["r_out"])
    base = base.cut(rbox(cx, cy, floor, cw, ch, base_h, job["r_in"]))
    adds = []
    for s in job["standoffs"]:
        adds.append(cyl(s["x"], s["y"], s["z0"] - 0.01, s["od"], s["h"] + 0.01))
    for l in job["ledges"]:
        adds.append(Part.makeBox(l["w"], l["d"], l["h"] + 0.01, V(l["x"], l["y"], l["z0"] - 0.01)))
    lid = job["lid"]
    for c in lid.get("columns", []):
        adds.append(cyl(c["x"], c["y"], floor - 0.01, c["od"], base_h - floor + 0.01))
        # tie the column into the corner walls
        gx = cx if c["x"] < ow / 2 else c["x"]
        gy = cy if c["y"] < oh / 2 else c["y"]
        adds.append(Part.makeBox(abs(c["x"] - cx) if c["x"] < ow / 2 else (cx + cw - c["x"]),
                                 c["od"] / 2, base_h - floor + 0.01,
                                 V(gx, c["y"] - c["od"] / 4, floor - 0.01)))
        adds.append(Part.makeBox(c["od"] / 2, abs(c["y"] - cy) if c["y"] < oh / 2 else (cy + ch - c["y"]),
                                 base_h - floor + 0.01, V(c["x"] - c["od"] / 4, gy, floor - 0.01)))
    if job["ears"]:
        tab_w = min(oh - 2 * job["r_out"], 18.0)
        for side in (-1, 1):
            x = -12.0 if side < 0 else ow - 2.0
            ear = rbox(x, oh / 2 - tab_w / 2, 0, 14.0, tab_w, 3.0, 3.0)
            hole_x = x + 6.0 if side < 0 else x + 8.0
            ear = ear.cut(rbox(hole_x - 2.2, oh / 2 - 3.5, -1, 4.4, 7.0, 5, 2.18))
            adds.append(ear)
    base = fuse_all(base, adds)
    cuts = []
    for s in job["standoffs"]:
        z0 = s.get("pilot_z", job["floor"] * 0.4)
        cuts.append(cyl(s["x"], s["y"], z0, s["pilot"], s["h"] + job["floor"] - z0 + 1))
    for c in lid.get("columns", []):
        cuts.append(cyl(c["x"], c["y"], base_h - 10.0, c["pilot"], 11.0))
    for op in job["openings"] + job["side_slots"]:
        cuts.append(wall_cut(job, op))
    for f in job["feet"]:
        cuts.append(cyl(f["x"], f["y"], -0.01, f["d"], f["depth"] + 0.01))
    for c in snap_catches(job):
        gx, gy = (c["x"] - 0.5, c["y"]) if c["axis"] == 0 else (c["x"], c["y"] - 0.5)
        cuts.append(cyl(gx, gy, c["z"], 2 * c["groove_r"], c["len"] + 1.0, c["axis"]))
    notch = lid.get("notch")
    if notch:
        # A shallow slot in the top of the front wall's outer half, to lever the lid off.
        cuts.append(rbox(notch["x"] - notch["w"] / 2, -1.0, base_h - notch["h"], notch["w"], 1.0 + job["wall"] / 2,
                         notch["h"] + 1.0, 0.0))
    return cut_all(base, cuts)


def snap_catches(job):
    """The bumps on a snap-fit lid's skirt; the base gets a groove at each. Wall-inner-face coordinates."""
    lid = job["lid"]
    if lid["type"] != "snap":
        return []
    cx, cy, cw, ch = job["cavity"]
    sn = lid["snap"]
    out = []
    for (x, y, axis, length) in ((cx + cw / 2, cy, 0, sn["len_x"]), (cx + cw / 2, cy + ch, 0, sn["len_x"]),
                                 (cx, cy + ch / 2, 1, sn["len_y"]), (cx + cw, cy + ch / 2, 1, sn["len_y"])):
        if length <= 0:
            continue
        if axis == 0:
            out.append({"x": x - length / 2, "y": y, "z": sn["z"], "len": length, "axis": 0,
                        "r": sn["r"], "groove_r": sn["groove_r"], "inward": 1 if y == cy else -1})
        else:
            out.append({"x": x, "y": y - length / 2, "z": sn["z"], "len": length, "axis": 1,
                        "r": sn["r"], "groove_r": sn["groove_r"], "inward": 1 if x == cx else -1})
    return out


LABEL_GAP = 1.0    # plain lid kept between the engraved letters and a hole, slot or screw head
LABEL_EDGE = 2.0   # and between the letters and the lid's outer edge
LABEL_MIN = 3.0    # the smallest text size the label is shrunk to when it fits nowhere


def label_text(job, size):
    """The label's letters as one solid, centred on the origin, cut lab["depth"] into the lid's top."""
    lab = job["label"]
    chars = Part.makeWireString(lab["text"], lab["font"], size, 0)
    faces = []
    for wires in chars:   # one list of wires per character
        wires = [w for w in wires if w.isClosed()]
        if wires:
            faces.append(Part.makeFace(wires, "Part::FaceMakerBullseye"))
    if not faces:
        return None
    comp = Part.makeCompound(faces)
    bb = comp.BoundBox
    comp.translate(V(-(bb.XMin + bb.XMax) / 2, -(bb.YMin + bb.YMax) / 2, job["base_h"] + job["lid_t"] - lab["depth"]))
    return comp.extrude(V(0, 0, lab["depth"] + 0.5))


def place_label(job, features):
    """Engrave the label where no letter comes within LABEL_GAP of a lid feature (an LED window, a button hole,
    a vent, a screw head) or within LABEL_EDGE of the lid's edge: where it was asked for when that is clear, else
    at the nearest clear spot, else smaller. features: (name, solid) of every cut through the lid.
    Returns the label's solid, or None when it fits nowhere; REPORT["label"] says where it went."""
    lab = job.get("label")
    if not lab:
        return None
    ow, oh = job["outer"]
    flat = [(n, s, s.BoundBox) for n, s in features if s is not None]
    exact = [0]       # exact distance checks made, capped so a crowded lid stays quick

    def hits(solid, x, y):
        """The features the letters at (x, y) come too close to: bounding boxes first, exact distance after."""
        bb = solid.BoundBox
        x0, x1, y0, y1 = bb.XMin + x, bb.XMax + x, bb.YMin + y, bb.YMax + y
        near = [(n, s) for n, s, fb in flat if x0 - LABEL_GAP < fb.XMax and fb.XMin < x1 + LABEL_GAP
                and y0 - LABEL_GAP < fb.YMax and fb.YMin < y1 + LABEL_GAP]
        if not near:
            return []
        exact[0] += 1
        moved = solid.copy()
        moved.translate(V(x, y, 0))
        return [n for n, s in near if moved.distToShape(s)[0] < LABEL_GAP - 1e-6]

    try:
        size = float(lab["size"])
        hits_at_ask = None
        while size >= LABEL_MIN - 1e-6:
            solid = label_text(job, size)
            if solid is None:
                return None
            bb = solid.BoundBox
            # where the label's centre may go so every letter stays LABEL_EDGE inside the lid
            xs = (LABEL_EDGE - bb.XMin, ow - LABEL_EDGE - bb.XMax)
            ys = (LABEL_EDGE - bb.YMin, oh - LABEL_EDGE - bb.YMax)
            if xs[0] <= xs[1] and ys[0] <= ys[1]:
                want = (min(max(lab["x"], xs[0]), xs[1]), min(max(lab["y"], ys[0]), ys[1]))
                if hits_at_ask is None:
                    hits_at_ask = hits(solid, lab["x"], lab["y"]) if want == (lab["x"], lab["y"]) else ["the lid's edge"]
                # nearest first, on a 0.5 mm grid
                exact[0] = 0
                grid = [(xs[0] + 0.5 * i, ys[0] + 0.5 * j) for i in range(int((xs[1] - xs[0]) / 0.5) + 1)
                        for j in range(int((ys[1] - ys[0]) / 0.5) + 1)] or [want]
                grid.sort(key=lambda p: (p[0] - want[0]) ** 2 + (p[1] - want[1]) ** 2)
                grid.insert(0, want)      # where it was asked for (or the nearest spot inside the edge) first
                for x, y in grid:
                    found = hits(solid, x, y)
                    if not found:
                        solid.translate(V(x, y, 0))
                        moved = math.hypot(x - lab["x"], y - lab["y"])
                        REPORT["label"] = {"x": round(x, 2), "y": round(y, 2), "size": size, "moved_mm": round(moved, 1)}
                        if moved > 0.05 or size < float(lab["size"]):
                            what = ", ".join(dict.fromkeys(hits_at_ask or [])) or "the lid's features"
                            NOTES.append("The label \"%s\" would have run into %s, so it was %s." % (
                                lab["text"], what,
                                ("moved %.1f mm" % moved if moved > 0.05 else "")
                                + (" and " if moved > 0.05 and size < float(lab["size"]) else "")
                                + ("made %g mm high (asked %g mm)" % (size, float(lab["size"]))
                                   if size < float(lab["size"]) else "")))
                        return solid
                    if exact[0] > 400:
                        break
            size = round(size * 0.85, 2)
        NOTES.append("The label \"%s\" was left off: it fits nowhere on the lid clear of the holes, vents and "
                     "screws, even at %g mm. Use shorter text, or move or drop a lid hole." % (lab["text"], LABEL_MIN))
        REPORT["label"] = None
        return None
    except Exception as ex:  # noqa: BLE001
        NOTES.append("The label could not be engraved (%s)." % ex)
        return None


def build_lid(job):
    ow, oh = job["outer"]
    cx, cy, cw, ch = job["cavity"]
    base_h, lid_t = job["base_h"], job["lid_t"]
    lid = job["lid"]
    say("Building the lid")
    shape = rbox(0, 0, base_h, ow, oh, lid_t, job["r_out"])
    adds = []
    if lid["type"] == "lip" and lid["lip_d"] > 0:
        fit, d, w = lid["fit"], lid["lip_d"], lid["lip_w"]
        outer = rbox(cx + fit, cy + fit, base_h - d, cw - 2 * fit, ch - 2 * fit, d + 0.01, max(job["r_in"] - fit, 0.3))
        inner = rbox(cx + fit + w, cy + fit + w, base_h - d - 0.01, cw - 2 * fit - 2 * w, ch - 2 * fit - 2 * w,
                     d + 0.03, max(job["r_in"] - fit - w, 0.2))
        adds.append(outer.cut(inner))
    if lid["type"] == "snap":
        fit, d, w = lid["fit"], lid["lip_d"], lid["lip_w"]
        outer = rbox(cx + fit, cy + fit, base_h - d, cw - 2 * fit, ch - 2 * fit, d + 0.01, max(job["r_in"] - fit, 0.3))
        inner = rbox(cx + fit + w, cy + fit + w, base_h - d - 0.01, cw - 2 * fit - 2 * w, ch - 2 * fit - 2 * w,
                     d + 0.03, max(job["r_in"] - fit - w, 0.2))
        adds.append(outer.cut(inner))
        for c in snap_catches(job):
            # A rounded bump centred on the skirt's outer face, so it sticks out c["r"] into the wall's groove.
            if c["axis"] == 0:
                adds.append(cyl(c["x"], c["y"] + c["inward"] * fit, c["z"], 2 * c["r"], c["len"], 0))
            else:
                adds.append(cyl(c["x"] + c["inward"] * fit, c["y"], c["z"], 2 * c["r"], c["len"], 1))
    for p in job["posts"]:
        adds.append(cyl(p["x"], p["y"], p["z0"], p["d"], p["z1"] - p["z0"] + 0.01))
    shape = fuse_all(shape, adds)
    features = []     # (name, solid) of every cut through the lid; the label keeps clear of them all
    depth = lid_t + lid.get("lip_d", 0) + 2
    for h in job["lid_holes"]:
        z = base_h - lid.get("lip_d", 0) - 1
        name = "the lid hole for %s" % h.get("name", "a part")
        if h["shape"] == "rect":
            features.append((name, rbox(h["x"] - h["w"] / 2, h["y"] - h["h"] / 2, z, h["w"], h["h"], depth,
                                        min(1.0, min(h["w"], h["h"]) / 2 - 0.02))))
        else:
            features.append((name, cyl(h["x"], h["y"], z, h["d"], depth)))
    for s in job["lid_slots"]:
        features.append(("a lid vent", rbox(s["x"], s["y"], base_h - 1, s["w"], s["h"], lid_t + 2, s["w"] / 2 - 0.02)))
    for c in lid.get("columns", []):
        features.append(("a lid screw", cyl(c["x"], c["y"], base_h - 1, c["clear"], lid_t + 2)))
        cb = min(1.2, lid_t - 0.6)
        if cb > 0.2:
            features.append(("a lid screw", cyl(c["x"], c["y"], base_h + lid_t - cb, c["head"] + 0.4, cb + 1)))
    return cut_all(shape, [s for _, s in features] + [place_label(job, features)])


def board_and_blocks(job):
    b = job["board"]
    board = rbox(b["x"], b["y"], b["z"], b["w"], b["h"], b["t"], b["r"])
    board = cut_all(board, [cyl(h["x"], h["y"], b["z"] - 1, h["d"], b["t"] + 2) for h in b["holes"]])
    blocks = [(k, Part.makeBox(k["w"], k["d"], k["h"], V(k["x"], k["y"], k["z"]))) for k in job["blocks"]]
    return board, blocks


def mesh_to_stl(shape, path, fine=True):
    import MeshPart
    m = MeshPart.meshFromShape(Shape=shape, LinearDeflection=0.02 if fine else 0.08,
                               AngularDeflection=0.2 if fine else 0.4, Relative=False)
    m.write(path)


def main(job):
    out, name = job["out_dir"], job["name"]
    base = build_base(job)
    lid = build_lid(job)
    board, blocks = board_and_blocks(job)
    say("Checking for collisions")
    collisions = []
    for k, blk in blocks:
        for what, solid in (("base", base), ("lid", lid)):
            v = blk.common(solid).Volume
            if v > 0.02:
                collisions.append({"part": k["ref"], "with": what, "overlap_mm3": round(v, 2)})
    for what, solid in (("base", base), ("lid", lid)):
        v = board.common(solid).Volume
        if v > 0.02:
            collisions.append({"part": "board", "with": what, "overlap_mm3": round(v, 2)})
    v = base.common(lid).Volume
    if v > 0.02:
        collisions.append({"part": "lid", "with": "base", "overlap_mm3": round(v, 2)})
    for what, s in (("base", base), ("lid", lid)):
        if not s.isValid():
            NOTES.append("FreeCAD reports the %s solid as invalid; check it before printing." % what)

    say("Exporting STEP, STL and the FreeCAD document")
    files = {}
    p = lambda suffix: os.path.join(out, "%s-enclosure-%s" % (name, suffix))  # noqa: E731
    base.exportStep(p("base.step")); files["base_step"] = p("base.step")
    lid.exportStep(p("lid.step")); files["lid_step"] = p("lid.step")
    mesh_to_stl(base, p("base.stl")); files["base_stl"] = p("base.stl")
    lid_print = lid.copy()
    lid_print.rotate(V(0, 0, 0), V(1, 0, 0), 180)
    bb = lid_print.BoundBox
    lid_print.translate(V(-bb.XMin, -bb.YMin, -bb.ZMin))
    mesh_to_stl(lid_print, p("lid.stl")); files["lid_stl"] = p("lid.stl")

    doc = App.newDocument("Enclosure")
    for label, shape in (("Base", base), ("Lid", lid), ("Board", board),
                         ("Parts", Part.makeCompound([b for _, b in blocks]) if blocks else None)):
        if shape is not None:
            doc.addObject("Part::Feature", label).Shape = shape
    doc.recompute()
    doc.saveAs(p("model.FCStd")); files["freecad"] = p("model.FCStd")
    if not App.GuiUp:
        App.closeDocument(doc.Name)

    prev_dir = os.path.join(out, "preview")
    os.makedirs(prev_dir, exist_ok=True)
    preview = []
    groups = [("Base", "base", None, base), ("Lid", "lid", None, lid), ("Board", "board", None, board)]
    kinds = {}
    for k, blk in blocks:
        kinds.setdefault(k["kind"], []).append(blk)
    for kind, solids in kinds.items():
        groups.append(("Parts (%s)" % kind, "parts", kind, Part.makeCompound(solids)))
    for label, group, kind, shape in groups:
        f = os.path.join(prev_dir, "%s-%s.stl" % (group, kind or "all"))
        mesh_to_stl(shape, f, fine=False)
        preview.append({"file": f, "name": label, "group": group, "kind": kind})
    return {"volumes_mm3": {"base": round(base.Volume, 1), "lid": round(lid.Volume, 1)},
            "collisions": collisions, "notes": NOTES, "files": files, "preview": preview, **REPORT}


def run():
    job = globals().get("JOB")
    if job is None:
        with open(os.environ["WB_ENCLOSURE_JOB"]) as f:
            job = json.load(f)
    report_path = os.path.join(job["out_dir"], "enclosure-report.json")
    try:
        report = main(job)
    except Exception as ex:  # noqa: BLE001
        report = {"error": "%s: %s" % (type(ex).__name__, ex), "trace": traceback.format_exc()}
    with open(report_path, "w") as f:
        json.dump(report, f, indent=1)
    say("Done" if "error" not in report else "Failed: " + report["error"])


run()
