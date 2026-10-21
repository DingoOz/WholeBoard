"""3D-printable enclosures, designed by the AI and built in FreeCAD.

The flow:
1. `context()` reads the finished board and lists what an enclosure designer needs: board size,
   mounting holes, and every part with its position, estimated height and nearest edge.
2. The AI writes an enclosure spec (see SPEC_DOC), starting from `suggest()` if it likes.
3. `validate()` checks the spec. `resolve()` turns it into exact geometry: every box, cylinder and
   cut in enclosure coordinates.
4. `build()` runs FreeCAD's command-line mode on `freecad_enclosure.py` with that geometry. FreeCAD
   builds the base and lid, checks every part against them for collisions and exports STEP, STL and
   a FreeCAD document.

Board coordinates (what the AI uses): x_mm right and y_mm down from the board's top-left corner,
like component placements. Enclosure coordinates (inside FreeCAD): X right, Y towards the board's
top edge, Z up from the bottom of the base, in millimetres.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

from . import models3d

SPEC_DOC = """{
  "wall_mm": 2.0, "floor_mm": 1.6, "lid_mm": 1.6,
  "gap_mm": 0.6,              # air between the board's parts and the walls
  "standoff_mm": null,        # board bottom above the floor; null = automatic (3-4 mm)
  "headroom_mm": 2.0,         # air above the tallest part (or set inner_height_mm instead)
  "inner_height_mm": null,    # board top to the underside of the lid
  "corner_radius_mm": 2.0,    # rounding of the outside vertical edges
  "lid": {"type": "lip" | "screws" | "snap", "fit_mm": 0.2, "lip_depth_mm": 2.5, "screw": "M3" | "M2.5" | "M2"},
                              # snap: a deeper skirt with bumps that click into grooves in the walls
                              # (no screws; a notch in the front wall to lever it off)
  "board_fixing": "screws" | "ledges",   # screws need mounting holes on the board
  "fastener": "self_tapping" | "heat_insert",
  "part_heights": {"J2": 8.5},           # datasheet heights above the board, override estimates
  "openings": [                          # holes in the side walls
    {"ref": "J1"},                       # sized and placed for that part on its nearest wall
    {"ref": "J1", "wall": "left", "width_mm": 13, "height_mm": 7, "shape": "rect" | "round", "margin_mm": 0.8},
    {"wall": "right", "at_mm": 12, "z_mm": 3, "width_mm": 6, "height_mm": 4, "shape": "rect", "note": "cable"}
  ],
  "lid_holes": [
    {"ref": "D1", "diameter_mm": 3},
    {"ref": "SW1", "shape": "rect", "width_mm": 9, "height_mm": 4},
    {"x_mm": 20, "y_mm": 10, "diameter_mm": 3, "note": "light pipe"}
  ],
  "vents": {"lid": false, "sides": false},
  "rubber_feet": false,                  # pockets under the base for 10 mm stick-on feet
  "mounting_ears": false,                # tabs with screw holes for fixing the box to a surface
  "label": {"text": "BLINKER", "size_mm": 6, "x_mm": null, "y_mm": null}   # engraved in the lid; null = centred
}
Walls are named as seen in the board's top view: "left", "right", "top" (y_mm = 0 side) and "bottom".
For a custom opening, at_mm is the position along the wall in board coordinates (y_mm on the left
and right walls, x_mm on the top and bottom walls) and z_mm is the opening's centre height above the
board's top surface (negative is below the board)."""

WALLS = ("left", "right", "top", "bottom")
SCREWS = {"M2": (1.6, 2.4, 4.0, 2.4), "M2.5": (2.1, 2.9, 4.8, 3.0), "M3": (2.5, 3.4, 6.0, 3.6)}
# screw: (self-tapping pilot, clearance hole, head diameter, heat-insert hole)
LIMITS = {"wall_mm": (1.2, 6), "floor_mm": (1.0, 6), "lid_mm": (1.0, 6), "gap_mm": (0.2, 10),
          "standoff_mm": (1.5, 40), "headroom_mm": (0, 50), "inner_height_mm": (1, 150),
          "corner_radius_mm": (0, 15)}
PORT_MARGIN = 0.8       # opening margin around a connector body
PORT_REACH = 6.0        # a connector this close to a board edge gets an opening in that wall

# (pattern on the lower-case footprint id, height mm, kind, extra)
# kinds: port = side opening, led = lid window, user = lid opening (buttons, switches, pots),
#        header = lid opening for cables, part = nothing, none = no body (mounting holes)
_RULES: list[tuple[str, float, str, dict]] = [
    (r"mountinghole|fiducial", 0, "none", {}),
    (r"testpoint", 0.5, "part", {}),
    (r"usb_c", 3.3, "port", {"min": (12.5, 7.0)}),
    (r"usb_micro", 3.0, "port", {"min": (11.0, 7.5)}),
    (r"usb_mini", 4.0, "port", {"min": (11.0, 8.0)}),
    (r"usb_a", 7.0, "port", {"min": (16.0, 9.5)}),
    (r"usb_b", 11.0, "port", {"min": (14.0, 13.0)}),
    (r"barreljack|dc_?jack", 11.0, "port", {"min": (11.5, 11.5), "round": True}),
    (r"jack_3\.5|audio|pj-3", 5.5, "port", {"min": (8.0, 8.0), "round": True}),
    (r"rj45|rj11", 13.5, "port", {"min": (17.0, 15.0)}),
    (r"sma_|bnc", 10.0, "port", {"min": (9.0, 9.0), "round": True}),
    (r"terminalblock.*vertical", 10.0, "header", {}),
    (r"terminalblock", 10.0, "port", {}),
    (r"(pinheader|pinsocket).*horizontal", 2.6, "port", {}),
    (r"pinsocket", 8.5, "header", {}),
    (r"pinheader", 8.5, "header", {}),
    (r"(jst|molex|conn_|connector).*horizontal", 4.5, "port", {}),
    (r"(jst|molex|conn_|connector)", 6.5, "header", {}),
    (r"sw_slide.*(angled|right)", 4.0, "port", {}),
    (r"sw_slide", 8.5, "user", {"slot": True}),
    (r"(sw_push|button|tact).*(angled|right)", 4.0, "port", {}),
    (r"sw_push|button|tact", 5.0, "user", {}),
    (r"sw_dip", 4.0, "user", {"slot": True}),
    (r"potentiometer", 12.0, "user", {"hole": 7.5}),
    (r"rotary_encoder", 20.0, "user", {"hole": 7.5}),
    (r"led_d5\.0|led_tht.*5\.0|led_d5mm", 8.6, "led", {"hole": 5.4}),
    (r"led_d3\.0|led_tht.*3\.0|led_d3mm", 5.3, "led", {"hole": 3.4}),
    (r"led_tht", 8.6, "led", {"hole": 5.4}),
    (r"led", 1.0, "led", {"hole": 3.0}),
    (r"buzzer", 9.5, "part", {}),
    (r"relay", 15.5, "part", {}),
    (r"cr2032|coin|cr1220", 6.0, "part", {}),
    (r"18650", 21.0, "part", {}),
    (r"battery", 12.0, "part", {}),
    (r"to-220.*vertical", 19.0, "part", {}),
    (r"to-220", 4.6, "part", {}),
    (r"to-247", 21.0, "part", {}),
    (r"to-92", 5.2, "part", {}),
    (r"to-263|d2pak", 4.6, "part", {}),
    (r"to-252|dpak", 2.4, "part", {}),
    (r"sot-223", 1.8, "part", {}),
    (r"dip.*socket", 8.5, "part", {}),
    (r"dip-|dip_", 5.0, "part", {}),
    (r"crystal_smd|crystal.*smd", 1.2, "part", {}),
    (r"hc-?49.*u_|hc49-u", 13.5, "part", {}),
    (r"crystal", 4.5, "part", {}),
    (r"oled|display|lcd", 4.0, "part", {}),
    (r"module", 3.5, "part", {}),
    (r"fuse", 1.5, "part", {}),
    (r"resistor_tht.*vertical", 11.0, "part", {}),
    (r"resistor_tht", 3.0, "part", {}),
    (r"c_disc", 8.0, "part", {}),
    (r"c_rect", 10.0, "part", {}),
    (r"diode_tht", 3.5, "part", {}),
    (r"d_sm[abc]", 2.4, "part", {}),
    (r"soic|sop|ssop|tssop|msop", 1.75, "part", {}),
    (r"qfn|dfn|lga|bga|wson", 1.0, "part", {}),
    (r"qfp", 1.6, "part", {}),
    (r"sot|sod", 1.2, "part", {}),
    (r"_(0201|0402|0603|0805)_", 1.0, "part", {}),
    (r"_(1206|1210|1812|2010|2512)_", 1.3, "part", {}),
]


def classify(fp_id: str, tht: bool, height_override: float | None = None) -> dict:
    """Estimate a part's height and what the enclosure should do for it."""
    s = fp_id.lower()
    height, kind, extra, estimated = (10.0 if tht else 2.5), "part", {}, True
    m = re.search(r"cp_radial_d(\d+(?:\.\d+)?)mm", s)
    m2 = re.search(r"cp_elec_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", s)
    m3 = re.search(r"_h(\d+(?:\.\d+)?)mm", s)
    m4 = re.search(r"_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)mm", s)
    if m:
        d = float(m.group(1))
        height = max(11.0, 2.0 * d - 2.0)
    elif m2:
        height = float(m2.group(2)) + 0.3
    else:
        for pat, h, k, ex in _RULES:
            if re.search(pat, s):
                height, kind, extra = h, k, ex
                break
        else:
            if m3:
                height = float(m3.group(1))
            elif m4 and "inductor" in s:
                height = float(m4.group(3))
    if m3 and kind not in ("none",):
        height = float(m3.group(1))
    if height_override is not None:
        height, estimated = float(height_override), False
    return {"height": height, "kind": kind, "extra": extra, "estimated": estimated}




# ----------------------------------------------------------------------------------- board facts
def context(geo: dict, spec: dict, part_heights: dict | None = None) -> dict:
    """What the AI needs to design an enclosure, in board coordinates (mm from the top-left)."""
    bb = geo.get("bbox")
    if not bb:
        raise ValueError("the board has no outline")
    bx0, by0, bx1, by1 = bb
    board = spec.get("board") or {}
    comps = {c["ref"]: c for c in spec.get("components") or []}
    heights = {**{r: c["height_mm"] for r, c in comps.items() if c.get("height_mm") is not None},
               **(part_heights or {})}
    tht = {p["ref"] for p in geo["pads"] if p.get("kind") == "thru_hole"}
    holes, parts = [], []
    for f in geo["footprints"]:
        if "mountinghole" in f["lib"].lower():
            drills = [p["drill"] for p in geo["pads"] if p["ref"] == f["ref"] and p.get("drill")]
            holes.append({"ref": f["ref"], "x_mm": round(f["x"] - bx0, 2), "y_mm": round(f["y"] - by0, 2),
                          "diameter_mm": max(drills) if drills else 3.2})
            continue
        if not f.get("court"):
            continue
        info = classify(f["lib"], f["ref"] in tht, heights.get(f["ref"]))
        if info["kind"] == "none":
            continue
        source = "estimate" if info["estimated"] else "datasheet"
        if info["estimated"]:
            mh, msrc = models3d.part_height(f, comps.get(f["ref"], {}).get("lcsc"))
            if mh is not None:
                info["height"], info["estimated"], source = mh, False, msrc
        c = f["court"]
        box = [round(c[0] - bx0, 2), round(c[1] - by0, 2), round(c[2] - bx0, 2), round(c[3] - by0, 2)]
        dists = {"left": box[0], "right": round(bx1 - bx0 - box[2], 2), "top": box[1],
                 "bottom": round(by1 - by0 - box[3], 2)}
        edge = min(dists, key=dists.get)
        parts.append({
            "ref": f["ref"], "value": comps.get(f["ref"], {}).get("value", ""), "footprint": f["lib"],
            "x_mm": round(f["x"] - bx0, 2), "y_mm": round(f["y"] - by0, 2), "rotation": f.get("rot", 0),
            "side": "bottom" if f["side"] == "B.Cu" else "top", "courtyard_mm": box,
            "height_mm": info["height"], "height_estimated": info["estimated"], "height_source": source,
            "kind": info["kind"],
            "through_hole": f["ref"] in tht, "nearest_wall": edge, "distance_to_edge_mm": dists[edge],
            "_extra": info["extra"],
        })
    return {"board": {"width_mm": round(bx1 - bx0, 2), "height_mm": round(by1 - by0, 2),
                      "thickness_mm": float(board.get("thickness_mm") or 1.6),
                      "corner_radius_mm": float(board.get("corner_radius_mm") or 0)},
            "mounting_holes": holes, "parts": parts}


def public_context(ctx: dict) -> dict:
    """The context without internal fields, for the AI."""
    return {**ctx, "parts": [{k: v for k, v in p.items() if not k.startswith("_")} for p in ctx["parts"]]}


def suggest(ctx: dict) -> dict:
    """A sensible starting enclosure the AI can adjust."""
    openings, lid_holes = [], []
    for p in ctx["parts"]:
        if p["side"] != "top" and p["kind"] != "port":
            continue
        if p["kind"] == "port" and p["distance_to_edge_mm"] <= PORT_REACH:
            openings.append({"ref": p["ref"]})
        elif p["kind"] == "led":
            lid_holes.append({"ref": p["ref"], "diameter_mm": p["_extra"].get("hole", 3.0)})
        elif p["kind"] == "user" and p["_extra"].get("slot"):
            c = p["courtyard_mm"]
            lid_holes.append({"ref": p["ref"], "shape": "rect", "width_mm": round(c[2] - c[0] + 1, 1),
                              "height_mm": round(c[3] - c[1] + 1, 1)})
        elif p["kind"] == "user":
            lid_holes.append({"ref": p["ref"], "diameter_mm": p["_extra"].get("hole", 4.0)})
        elif p["kind"] == "header":
            c = p["courtyard_mm"]
            lid_holes.append({"ref": p["ref"], "shape": "rect", "width_mm": round(c[2] - c[0] + 1, 1),
                              "height_mm": round(c[3] - c[1] + 1, 1), "note": "cable opening"})
    return {"wall_mm": 2.0, "floor_mm": 1.6, "lid_mm": 1.6, "gap_mm": 0.6, "headroom_mm": 2.0,
            "corner_radius_mm": 2.0, "lid": {"type": "lip", "fit_mm": 0.2, "lip_depth_mm": 2.5},
            "board_fixing": "screws" if ctx["mounting_holes"] else "ledges", "fastener": "self_tapping",
            "openings": openings, "lid_holes": lid_holes, "vents": {"lid": False, "sides": False},
            "rubber_feet": False, "mounting_ears": False}


def validate(enc: dict, ctx: dict) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    if not isinstance(enc, dict):
        return ["the enclosure spec must be a JSON object"], []
    refs = {p["ref"] for p in ctx["parts"]}
    for k, (lo, hi) in LIMITS.items():
        v = enc.get(k)
        if v is None:
            continue
        if not isinstance(v, (int, float)) or not lo <= v <= hi:
            errors.append(f"{k} must be a number from {lo} to {hi}")
    lid = enc.get("lid") or {}
    if lid.get("type", "lip") not in ("lip", "screws", "snap"):
        errors.append('lid.type must be "lip", "screws" or "snap"')
    if lid.get("screw", "M3") not in SCREWS:
        errors.append("lid.screw must be one of " + ", ".join(SCREWS))
    if enc.get("board_fixing", "screws") not in ("screws", "ledges"):
        errors.append('board_fixing must be "screws" or "ledges"')
    if enc.get("board_fixing") == "screws" and not ctx["mounting_holes"]:
        errors.append('board_fixing "screws" needs mounting holes on the board; use "ledges" or add holes')
    if enc.get("fastener", "self_tapping") not in ("self_tapping", "heat_insert"):
        errors.append('fastener must be "self_tapping" or "heat_insert"')
    for r, h in (enc.get("part_heights") or {}).items():
        if r not in refs:
            warnings.append(f"part_heights: there is no part {r}")
        elif not isinstance(h, (int, float)) or not 0 <= h <= 100:
            errors.append(f"part_heights.{r} must be a height in mm")
    for i, o in enumerate(enc.get("openings") or []):
        where = f"openings[{i}]"
        if not isinstance(o, dict):
            errors.append(f"{where} must be an object")
            continue
        if o.get("wall") is not None and o["wall"] not in WALLS:
            errors.append(f"{where}.wall must be one of {', '.join(WALLS)}")
        if o.get("shape", "rect") not in ("rect", "round"):
            errors.append(f'{where}.shape must be "rect" or "round"')
        if o.get("ref"):
            if o["ref"] not in refs:
                errors.append(f"{where}: there is no part {o['ref']}")
        elif o.get("wall") is None or not all(isinstance(o.get(k), (int, float))
                                               for k in ("at_mm", "z_mm", "width_mm", "height_mm")):
            errors.append(f"{where} needs a ref, or wall, at_mm, z_mm, width_mm and height_mm")
    for i, h in enumerate(enc.get("lid_holes") or []):
        where = f"lid_holes[{i}]"
        if not isinstance(h, dict):
            errors.append(f"{where} must be an object")
            continue
        if h.get("ref") and h["ref"] not in refs:
            errors.append(f"{where}: there is no part {h['ref']}")
        if not h.get("ref") and not all(isinstance(h.get(k), (int, float)) for k in ("x_mm", "y_mm")):
            errors.append(f"{where} needs a ref, or x_mm and y_mm")
        if h.get("shape", "round") == "rect":
            if not all(isinstance(h.get(k), (int, float)) and h[k] > 0 for k in ("width_mm", "height_mm")):
                errors.append(f"{where}: a rect hole needs width_mm and height_mm")
        elif not isinstance(h.get("diameter_mm", 3), (int, float)):
            errors.append(f"{where}.diameter_mm must be a number")
    lab = enc.get("label")
    if lab is not None and (not isinstance(lab, dict) or not str(lab.get("text", "")).strip()):
        errors.append('label must look like {"text": "NAME", "size_mm": 6}')
    return errors, warnings


# ----------------------------------------------------------------------------------- geometry
def _font() -> str | None:
    """A TrueType font for engraved labels."""
    cands = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf", r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf",
    ]
    for c in cands:
        if Path(c).exists():
            return c
    for d in ("/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts")):
        if Path(d).is_dir():
            for f in Path(d).rglob("*.ttf"):
                return str(f)
    return None


def resolve(ctx: dict, enc: dict) -> dict:
    """Turn the AI's enclosure spec into exact solids for FreeCAD (enclosure coordinates)."""
    notes: list[str] = []
    b = ctx["board"]
    bw, bh, thick, r_board = b["width_mm"], b["height_mm"], b["thickness_mm"], b["corner_radius_mm"]
    heights = enc.get("part_heights") or {}
    parts = []
    for p in ctx["parts"]:
        p = dict(p)
        if p["ref"] in heights:
            p["height_mm"], p["height_estimated"] = float(heights[p["ref"]]), False
        parts.append(p)
    g = lambda k: float(enc[k]) if enc.get(k) is not None else None  # noqa: E731
    wall, floor, lid_t = g("wall_mm") or 2.0, g("floor_mm") or 1.6, g("lid_mm") or 1.6
    gap = g("gap_mm") if enc.get("gap_mm") is not None else 0.6
    lid = {"type": "lip", "fit_mm": 0.2, "lip_depth_mm": 2.5, "screw": "M3", **(enc.get("lid") or {})}
    pilot_st, clear, head, insert = SCREWS[lid["screw"]]
    if lid["type"] == "snap" and wall < 1.8:
        wall = 1.8   # room for a 0.75 mm groove
        notes.append("Snap-fit grooves need 1.8 mm walls; the walls were thickened.")

    # Cavity: the board and every courtyard plus the gap (board coordinates).
    c0x = min([0.0] + [p["courtyard_mm"][0] for p in parts]) - gap
    c0y = min([0.0] + [p["courtyard_mm"][1] for p in parts]) - gap
    c1x = max([bw] + [p["courtyard_mm"][2] for p in parts]) + gap
    c1y = max([bh] + [p["courtyard_mm"][3] for p in parts]) + gap
    col_d = 0.0
    if lid["type"] == "screws":
        col_d = (insert if enc.get("fastener") == "heat_insert" else pilot_st) + 4.4
        c0x -= col_d + 0.4
        c1x += col_d + 0.4
    cw, ch = c1x - c0x, c1y - c0y
    ow, oh = cw + 2 * wall, ch + 2 * wall
    r_out = float(enc["corner_radius_mm"]) if enc.get("corner_radius_mm") is not None else 2.0
    r_out = min(r_out, ow / 2 - 0.5, oh / 2 - 0.5)
    r_in = max(0.0, r_out - wall)

    def X(x: float) -> float:   # board x -> enclosure X
        return x - c0x + wall

    def Y(y: float) -> float:   # board y -> enclosure Y (flipped so the top edge is at high Y)
        return c1y - y + wall

    top = [p for p in parts if p["side"] == "top"]
    bot = [p for p in parts if p["side"] == "bottom"]
    h_top = max([p["height_mm"] for p in top] or [1.0])
    h_bot = max([p["height_mm"] for p in bot] or [0.0])
    any_tht = any(p["through_hole"] for p in parts)
    standoff = g("standoff_mm")
    if standoff is None:
        standoff = max(4.0 if any_tht else 3.0, h_bot + 1.0)
    elif bot and standoff < h_bot + 0.5:
        notes.append(f"The standoff ({standoff:g} mm) is lower than the tallest part under the board ({h_bot:g} mm).")
    z_board = floor + standoff
    z_top = z_board + thick
    lip_d = float(lid["lip_depth_mm"]) if lid["type"] == "lip" else 0.0
    if lid["type"] == "snap":
        lip_d = max(float(lid["lip_depth_mm"]), 4.0)
    edge_h = max([p["height_mm"] for p in top if min(p["courtyard_mm"][0] - c0x, c1x - p["courtyard_mm"][2],
                  p["courtyard_mm"][1] - c0y, c1y - p["courtyard_mm"][3]) < gap + 2.0] or [0.0])
    if enc.get("inner_height_mm") is not None:
        z_inner = z_top + float(enc["inner_height_mm"])
        if z_inner < z_top + h_top:
            notes.append(f"inner_height_mm is less than the tallest part ({h_top:g} mm); the lid will hit it.")
    else:
        headroom = g("headroom_mm") if enc.get("headroom_mm") is not None else 2.0
        z_inner = z_top + h_top + headroom
    if lip_d and z_inner < z_top + edge_h + lip_d + 0.3:
        z_inner = z_top + edge_h + lip_d + 0.3
        notes.append("The box was made taller so the lid's lip clears the parts near the walls.")
    base_h = z_inner

    job: dict = {"outer": [ow, oh], "r_out": r_out, "wall": wall, "floor": floor, "base_h": base_h,
                 "cavity": [wall, wall, cw, ch], "r_in": r_in, "lid_t": lid_t,
                 "z_board": z_board, "z_top": z_top, "thick": thick}

    # Board support.
    fixing = enc.get("board_fixing") or ("screws" if ctx["mounting_holes"] else "ledges")
    ins = enc.get("fastener") == "heat_insert"
    standoffs, ledges, posts = [], [], []
    feet_depth = min(0.8, floor - 0.8) if enc.get("rubber_feet") else 0.0
    pilot_z = max(floor * 0.4, feet_depth + 0.5) if feet_depth >= 0.4 else floor * 0.4
    if fixing == "screws":
        for hole in ctx["mounting_holes"]:
            d = hole["diameter_mm"]
            pilot = d * 1.25 if ins else d * 0.78
            standoffs.append({"x": X(hole["x_mm"]), "y": Y(hole["y_mm"]), "od": pilot + 3.4, "pilot": pilot,
                              "z0": floor, "h": standoff, "pilot_z": pilot_z})
    else:
        s = 3.0
        for (x, y, sx, sy) in ((0, 0, 1, 1), (bw, 0, -1, 1), (0, bh, 1, -1), (bw, bh, -1, -1)):
            sq = (min(x, x + sx * s), min(y, y + sy * s), max(x, x + sx * s), max(y, y + sy * s))

            def hits(ps):
                return any(p["courtyard_mm"][0] < sq[2] and p["courtyard_mm"][2] > sq[0]
                           and p["courtyard_mm"][1] < sq[3] and p["courtyard_mm"][3] > sq[1] for p in ps)
            px0, py0 = X(sq[0]), Y(sq[3])
            if not hits(bot + [p for p in top if p["through_hole"]]):
                ledges.append({"x": px0, "y": py0, "w": s, "d": s, "z0": floor, "h": standoff})
            if not hits(top):
                posts.append({"x": px0 + s / 2, "y": py0 + s / 2, "d": 2.8, "z0": z_top + 0.1, "z1": base_h})
        if len(ledges) < 3:
            notes.append("Parts cover most board corners, so the board has little support. Add mounting holes.")
    job.update(standoffs=standoffs, ledges=ledges, posts=posts)

    # Lid.
    fit = float(lid.get("fit_mm", 0.2))
    job["lid"] = {"type": lid["type"], "fit": fit, "lip_d": lip_d, "lip_w": 1.2}
    if lid["type"] == "snap":
        # Bumps 0.6 mm proud of the skirt, 1.2 mm above its bottom edge, on each side long enough to flex.
        job["lid"]["lip_w"] = 1.3
        side = lambda n: round(min(16.0, n * 0.35), 1) if n >= 14 else 0.0  # noqa: E731
        job["lid"]["snap"] = {"r": 0.6, "groove_r": 0.75, "z": base_h - lip_d + 1.2,
                              "len_x": side(cw), "len_y": side(ch)}
        job["lid"]["notch"] = {"x": ow / 2, "w": min(12.0, ow * 0.3), "h": 1.4}
        if not (side(cw) or side(ch)):
            notes.append("The box is too small for snap catches; use a lip or screws.")
    if lid["type"] == "screws":
        cols = []
        for cx in (wall + col_d / 2 + 0.2, ow - wall - col_d / 2 - 0.2):
            for cy in (wall + col_d / 2 + 0.2, oh - wall - col_d / 2 - 0.2):
                cols.append({"x": cx, "y": cy, "od": col_d, "pilot": insert if ins else pilot_st,
                             "clear": clear, "head": head})
        job["lid"]["columns"] = cols

    # Side openings.
    def wall_box(wall_name: str, mid_along: float, span: float, z0: float, z1: float, shape: str) -> dict:
        z0, z1 = max(floor + 0.5, z0), min(base_h - 0.6, z1)
        return {"wall": wall_name, "u0": mid_along - span / 2, "u1": mid_along + span / 2, "z0": z0, "z1": z1,
                "shape": shape}
    openings, opening_info = [], []
    for o in enc.get("openings") or []:
        shape = o.get("shape", "rect")
        if o.get("ref"):
            p = next(p for p in parts if p["ref"] == o["ref"])
            c = p["courtyard_mm"]
            w = o.get("wall") or p["nearest_wall"]
            dist = {"left": c[0], "right": bw - c[2], "top": c[1], "bottom": bh - c[3]}[w]
            if dist > PORT_REACH:
                notes.append(f"{p['ref']} is {dist:.1f} mm from the {w} edge; its opening may be hard to reach.")
            m = float(o.get("margin_mm", PORT_MARGIN))
            mn = p["_extra"].get("min", (0, 0))
            if p["_extra"].get("round") and "shape" not in o:
                shape = "round"
            a, bb_ = (Y(c[3]), Y(c[1])) if w in ("left", "right") else (X(c[0]), X(c[2]))
            span = float(o.get("width_mm") or max(bb_ - a + 2 * m, mn[0]))
            zh = float(o.get("height_mm") or max(p["height_mm"] + 2 * m, mn[1]))
            zc = z_top + p["height_mm"] / 2 if p["side"] == "top" else z_board - p["height_mm"] / 2
            ob = wall_box(w, (a + bb_) / 2, span, zc - zh / 2, zc + zh / 2, shape)
            label = p["ref"]
        else:
            w = o["wall"]
            along = Y(float(o["at_mm"])) if w in ("left", "right") else X(float(o["at_mm"]))
            zc = z_top + float(o["z_mm"])
            ob = wall_box(w, along, float(o["width_mm"]), zc - float(o["height_mm"]) / 2,
                          zc + float(o["height_mm"]) / 2, shape)
            label = o.get("note") or "custom"
        ob["name"] = label
        openings.append(ob)
        opening_info.append({"for": label, "wall": w, "width_mm": round(ob["u1"] - ob["u0"], 1),
                             "height_mm": round(ob["z1"] - ob["z0"], 1), "shape": shape})
    job["openings"] = openings

    # Lid holes.
    holes, hole_info = [], []
    for h in enc.get("lid_holes") or []:
        if h.get("ref"):
            p = next(p for p in parts if p["ref"] == h["ref"])
            c = p["courtyard_mm"]
            cx, cy, what = X((c[0] + c[2]) / 2), Y((c[1] + c[3]) / 2), p["ref"]
            gap_above = base_h - z_top - p["height_mm"]
            if p["kind"] == "user" and gap_above > 1.5:
                notes.append(f"{p['ref']} sits {gap_above:.1f} mm below the lid; it needs a long actuator "
                             "or a printed button cap.")
        else:
            cx, cy, what = X(float(h["x_mm"])), Y(float(h["y_mm"])), h.get("note") or "custom"
        if h.get("shape") == "rect":
            hh = {"shape": "rect", "x": cx, "y": cy, "w": float(h["width_mm"]), "h": float(h["height_mm"])}
            size = f"{hh['w']:g} × {hh['h']:g} mm"
        else:
            hh = {"shape": "round", "x": cx, "y": cy, "d": float(h.get("diameter_mm", 3.0))}
            size = f"{hh['d']:g} mm"
        hh["name"] = what
        holes.append(hh)
        hole_info.append({"for": what, "shape": hh["shape"], "size": size})
    job["lid_holes"] = holes

    label = enc.get("label")
    job["label"] = None
    if label:
        font = _font()
        if font:
            lx = X(float(label["x_mm"])) if label.get("x_mm") is not None else ow / 2
            ly = Y(float(label["y_mm"])) if label.get("y_mm") is not None else oh / 2
            job["label"] = {"text": str(label["text"])[:40], "size": float(label.get("size_mm", 6)),
                            "x": lx, "y": ly, "depth": min(0.6, lid_t / 2), "font": font}
        else:
            notes.append("No TrueType font was found, so the label was left off.")

    lab_box = None
    if job["label"]:
        lw = len(job["label"]["text"]) * job["label"]["size"] * 0.65
        lh = job["label"]["size"] * 1.2
        lab_box = (job["label"]["x"] - lw / 2 - 1.5, job["label"]["y"] - lh / 2 - 1.5,
                   job["label"]["x"] + lw / 2 + 1.5, job["label"]["y"] + lh / 2 + 1.5)
        for hh in holes:
            r = hh.get("d", max(hh.get("w", 0), hh.get("h", 0))) / 2
            if lab_box[0] - r < hh["x"] < lab_box[2] + r and lab_box[1] - r < hh["y"] < lab_box[3] + r:
                notes.append(f"The label overlaps the lid hole for {hh['name']}; move it with label x_mm/y_mm.")
        if lw > ow - 4:
            notes.append("The label is wider than the lid; use a smaller size_mm or shorter text.")

    # Vents.
    vents = enc.get("vents") or {}
    lid_slots, side_slots = [], []
    if vents.get("lid"):
        pitch, sw, sl = 4.0, 1.6, max(6.0, min(14.0, oh * 0.45))
        n = int((ow * 0.6) // pitch)
        x0 = ow / 2 - (n - 1) * pitch / 2
        for i in range(n):
            vx = x0 + i * pitch
            if any(abs(vx - hh["x"]) < (hh.get("w", hh.get("d", 3)) / 2 + 2.5) for hh in holes):
                continue
            if lab_box and lab_box[0] - sw < vx < lab_box[2] + sw and lab_box[1] < oh / 2 + sl / 2 \
                    and lab_box[3] > oh / 2 - sl / 2:
                continue
            lid_slots.append({"x": vx - sw / 2, "y": oh / 2 - sl / 2, "w": sw, "h": sl})
    if vents.get("sides"):
        z0, z1 = z_top + 1.5, min(base_h - 2.0, z_top + 10)
        if z1 - z0 >= 3:
            for wname, length in (("left", oh), ("right", oh), ("top", ow), ("bottom", ow)):
                n = int((length * 0.5) // 4.0)
                start = length / 2 - (n - 1) * 2.0
                for i in range(n):
                    u = start + i * 4.0
                    if any(op["wall"] == wname and op["u0"] - 2.5 < u < op["u1"] + 2.5 for op in openings):
                        continue
                    side_slots.append({"wall": wname, "u0": u - 0.8, "u1": u + 0.8, "z0": z0, "z1": z1,
                                       "shape": "rect", "name": "vent"})
        else:
            notes.append("There is no room above the board for side vents.")
    job["lid_slots"] = lid_slots
    job["side_slots"] = side_slots

    job["feet"] = []
    if enc.get("rubber_feet"):
        depth = feet_depth
        if depth >= 0.4:
            ins_ = 6.5
            for fx in (ins_, ow - ins_):
                for fy in (ins_, oh - ins_):
                    job["feet"].append({"x": fx, "y": fy, "d": 10.4, "depth": depth})
        else:
            notes.append("The floor is too thin for rubber-foot pockets.")
    job["ears"] = bool(enc.get("mounting_ears"))

    # The board and part blocks, for the collision check and the preview.
    job["board"] = {"x": X(0), "y": Y(bh), "w": bw, "h": bh, "r": r_board, "z": z_board, "t": thick,
                    "holes": [{"x": X(hh["x_mm"]), "y": Y(hh["y_mm"]), "d": hh["diameter_mm"]}
                              for hh in ctx["mounting_holes"]]}
    blocks = []
    for p in parts:
        c = p["courtyard_mm"]
        w, d = max(0.4, c[2] - c[0] - 0.5), max(0.4, c[3] - c[1] - 0.5)
        hh = max(0.3, p["height_mm"])
        z = z_top if p["side"] == "top" else z_board - hh
        blocks.append({"ref": p["ref"], "kind": p["kind"], "x": X(c[0]) + 0.25, "y": Y(c[3]) + 0.25, "z": z,
                       "w": w, "d": d, "h": hh})
    job["blocks"] = blocks

    estimated = sorted(p["ref"] for p in parts if p["height_estimated"] and p["height_mm"] >= 3)
    if estimated:
        notes.append("Heights are estimates for " + ", ".join(estimated) + ". Put datasheet heights in part_heights.")
    tallest = max(parts, key=lambda p: p["height_mm"]) if parts else None
    job["summary"] = {
        "outer_mm": [round(ow, 1), round(oh, 1), round(base_h + lid_t, 1)],
        "inner_mm": [round(cw, 1), round(ch, 1), round(base_h - floor, 1)],
        "board_standoff_mm": round(standoff, 1),
        "board_top_to_lid_mm": round(base_h - z_top, 1),
        "tallest_part": f"{tallest['ref']} ({tallest['height_mm']:g} mm)" if tallest else None,
        "board_fixing": (f"{len(standoffs)} standoffs for {'heat-set inserts' if ins else 'self-tapping screws'}"
                         if standoffs else f"{len(ledges)} corner ledges and {len(posts)} lid posts"),
        "lid": {"lip": "press-fit lip", "snap": "snap-fit catches (lever off at the front notch)"}.get(
            lid["type"], f"4 {lid['screw']} screws"),
        "openings": opening_info, "lid_holes": hole_info,
        "extras": [x for x, on in (("lid vents", bool(lid_slots)), ("side vents", bool(side_slots)),
                                   ("rubber-foot pockets", bool(job["feet"])), ("mounting ears", job["ears"]),
                                   ("engraved label", bool(job["label"]))) if on],
        "notes": notes,
    }
    return job


# ----------------------------------------------------------------------------------- FreeCAD
TOOLS_DIR = Path(os.environ.get("WHOLEBOARD_HOME", Path.home() / ".wholeboard")) / "tools" / "freecad"


def find_freecad(cfg: dict | None = None) -> list[str] | None:
    """The command that runs FreeCAD without a window (as a list), or None."""
    def wrap(path: str) -> list[str] | None:
        p = Path(path)
        if not p.exists():
            return None
        if p.suffix.lower() == ".appimage":
            return [str(p), "freecadcmd"]
        if p.is_dir():
            for n in ("freecadcmd", "FreeCADCmd", "freecadcmd.exe", "FreeCADCmd.exe"):
                for sub in ("", "bin", "Contents/Resources/bin"):
                    q = p / sub / n
                    if q.exists():
                        return [str(q)]
            return None
        if p.stem.lower() == "freecad":   # the GUI binary: use its command-line sibling
            for n in ("freecadcmd", "FreeCADCmd", "freecadcmd.exe", "FreeCADCmd.exe"):
                if (p.parent / n).exists():
                    return [str(p.parent / n)]
        return [str(p)]

    configured = ((cfg or {}).get("tools") or {}).get("freecad")
    if configured:
        return wrap(configured)
    for n in ("freecadcmd", "FreeCADCmd"):
        if shutil.which(n):
            return [shutil.which(n)]
    images = sorted(TOOLS_DIR.glob("FreeCAD*.AppImage")) if TOOLS_DIR.is_dir() else []
    if images:
        return [str(images[-1]), "freecadcmd"]
    system = platform.system()
    cands: list[str] = []
    if system == "Darwin":
        for root in ("/Applications", str(Path.home() / "Applications")):
            cands += [f"{root}/FreeCAD.app/Contents/Resources/bin/freecadcmd",
                      f"{root}/FreeCAD.app/Contents/Resources/bin/FreeCADCmd"]
    elif system == "Windows":
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if not base:
                continue
            for d in sorted(Path(base).glob("FreeCAD*"), reverse=True) + sorted(Path(base, "Programs").glob("FreeCAD*"), reverse=True):
                cands += [str(d / "bin" / "freecadcmd.exe"), str(d / "bin" / "FreeCADCmd.exe")]
    else:
        cands += ["/usr/bin/freecadcmd", "/usr/bin/FreeCADCmd", "/usr/lib/freecad/bin/FreeCADCmd",
                  "/usr/lib/freecad-python3/bin/FreeCADCmd", "/snap/bin/freecad.cmd"]
        for d in (Path.home() / "Applications", Path.home() / "Downloads", Path("/opt")):
            if d.is_dir():
                imgs = sorted(d.glob("FreeCAD*.AppImage"))
                if imgs:
                    return [str(imgs[-1]), "freecadcmd"]
    for c in cands:
        if Path(c).exists():
            return [c]
    if system == "Linux" and shutil.which("flatpak"):
        try:
            if subprocess.run(["flatpak", "info", "org.freecad.FreeCAD"], capture_output=True, timeout=20).returncode == 0:
                return ["flatpak", "run", "--filesystem=home", "--command=FreeCADCmd", "org.freecad.FreeCAD"]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


_VERSION_CACHE: dict[str, str | None] = {}


def freecad_version(cmd: list[str]) -> str | None:
    key = " ".join(cmd)
    if key not in _VERSION_CACHE:
        try:
            out = subprocess.run(cmd + ["-c", "import FreeCAD;print('FCVER', '.'.join(FreeCAD.Version()[:3]))"],
                                 capture_output=True, text=True, timeout=120)
            m = re.search(r"FCVER ([\d.]+)", out.stdout + out.stderr)
            _VERSION_CACHE[key] = m.group(1) if m else None
        except (OSError, subprocess.TimeoutExpired):
            _VERSION_CACHE[key] = None
    return _VERSION_CACHE[key]


def run_freecad(job: dict, out_dir: Path, name: str, cfg: dict | None = None, log=print, cancel=None) -> dict:
    cmd = find_freecad(cfg)
    if not cmd:
        raise RuntimeError("FreeCAD was not found. Install FreeCAD 1.0 or newer (the installer can do this), "
                           "or set its path in Settings.")
    out_dir.mkdir(parents=True, exist_ok=True)
    job = {**job, "name": name, "out_dir": str(out_dir.resolve())}
    job_file = out_dir / "enclosure-job.json"
    job_file.write_text(json.dumps(job, indent=1))
    script = Path(__file__).with_name("freecad_enclosure.py")
    # The same build as a FreeCAD macro with the job built in, to open and tweak in FreeCAD.
    (out_dir / f"{name}-enclosure.FCMacro").write_text(
        "# WholeBoard enclosure. Run this macro in FreeCAD to rebuild the parts; edit JOB to change them.\n"
        "import json as _json\nJOB = _json.loads(" + repr(json.dumps(job)) + ")\n\n" + script.read_text())
    report_file = out_dir / "enclosure-report.json"
    report_file.unlink(missing_ok=True)
    env = {**os.environ, "WB_ENCLOSURE_JOB": str(job_file.resolve())}
    log("Running FreeCAD: " + " ".join(Path(c).name for c in cmd))
    proc = subprocess.Popen(cmd + [str(script)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env=env, cwd=str(out_dir))
    t0 = time.time()
    for line in proc.stdout:  # type: ignore[union-attr]
        line = line.rstrip()
        if line.startswith("WB:"):
            log(line[3:].strip())
        if cancel is not None and cancel.is_set():
            proc.kill()
            raise RuntimeError("cancelled")
        if time.time() - t0 > 900:
            proc.kill()
            raise RuntimeError("FreeCAD took longer than 15 minutes")
    proc.wait()
    if not report_file.exists():
        raise RuntimeError(f"FreeCAD exited with code {proc.returncode} without a report")
    report = json.loads(report_file.read_text())
    if report.get("error"):
        raise RuntimeError("FreeCAD: " + report["error"])
    return report


def read_stl(path: Path) -> tuple[list[float], list[int]]:
    """Read an STL (binary or ASCII) into shared vertices and triangle indices."""
    data = path.read_bytes()
    tris: list[tuple[float, ...]] = []
    if len(data) >= 84 and len(data) == 84 + 50 * struct.unpack("<I", data[80:84])[0]:
        n = struct.unpack("<I", data[80:84])[0]
        for i in range(n):
            tris.append(struct.unpack_from("<9f", data, 84 + 50 * i + 12))
    else:
        pts = [tuple(map(float, l.split()[1:4])) for l in data.decode(errors="ignore").splitlines()
               if l.strip().startswith("vertex")]
        tris = [pts[i] + pts[i + 1] + pts[i + 2] for i in range(0, len(pts) - 2, 3)]
    index: dict[tuple, int] = {}
    verts: list[float] = []
    out: list[int] = []
    for t in tris:
        for k in range(3):
            key = (round(t[3 * k], 3), round(t[3 * k + 1], 3), round(t[3 * k + 2], 3))
            i = index.get(key)
            if i is None:
                i = index[key] = len(verts) // 3
                verts.extend(round(v, 2) for v in key)
            out.append(i)
    return verts, out


PREVIEW_COLOURS = {"base": "#c9d1d9", "lid": "#9fb4c7", "board": "#1f7a4d", "port": "#b9c0c8", "led": "#e5534b",
                   "user": "#3a3f46", "header": "#2a2d31", "part": "#30343a"}


def build(geo: dict, spec: dict, enc: dict, out_dir: Path, name: str, cfg: dict | None = None,
          log=print, cancel=None) -> dict:
    """Validate the AI's enclosure, build it in FreeCAD and return a summary with any problems."""
    ctx = context(geo, spec, (enc or {}).get("part_heights"))
    errors, warnings = validate(enc, ctx)
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}
    job = resolve(ctx, enc)
    report = run_freecad(job, out_dir, name, cfg, log=log, cancel=cancel)
    meshes = []
    for m in report.get("preview", []):
        v, t = read_stl(Path(m["file"]))
        group = m["group"]
        meshes.append({"name": m["name"], "group": "board" if group not in ("base", "lid") else group,
                       "color": PREVIEW_COLOURS.get(m.get("kind") or group, "#30343a"), "v": v, "t": t})
    o = job["summary"]["outer_mm"]
    preview = out_dir / f"{name}-enclosure-preview.json"
    preview.write_text(json.dumps({"size": o, "meshes": meshes}, separators=(",", ":")))
    vol = report["volumes_mm3"]["base"] + report["volumes_mm3"]["lid"]
    summary = {**job["summary"], "ok": not report.get("collisions"),
               "collisions": report.get("collisions", []),
               "print_volume_cm3": round(vol / 1000, 1), "filament_g": round(vol / 1000 * 1.24, 1),
               "freecad_notes": report.get("notes", []), "warnings": warnings,
               "files": {k: str(v) for k, v in report["files"].items()}, "preview": str(preview)}
    summary["print"] = print_estimate(vol, (cfg or {}).get("printing"))
    (out_dir / f"{name}-enclosure.json").write_text(json.dumps(summary, indent=1))
    return summary


MATERIALS = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "ASA": 1.07, "TPU": 1.21}


def print_estimate(volume_mm3: float, settings: dict | None = None) -> dict:
    """Filament, time and cost to print the base and lid at home.

    The solid volume is mostly walls, which slicers print as solid perimeters, so grams follow the
    volume; time follows the volumetric rate the printer sustains plus heat-up per part."""
    s = {"material": "PLA", "price_per_kg": 25.0, "rate_cm3_h": 15.0, "power_w": 120.0, "price_kwh": 0.30,
         "machine_per_h": 0.0, **(settings or {})}
    density = MATERIALS.get(str(s["material"]).upper(), 1.24)
    cm3 = volume_mm3 / 1000
    grams = cm3 * density * 1.03            # skirt, purge and the odd failed first layer
    hours = cm3 / max(float(s["rate_cm3_h"]), 1.0) + 2 * 0.15
    filament = grams / 1000 * float(s["price_per_kg"])
    power = hours * float(s["power_w"]) / 1000 * float(s["price_kwh"])
    machine = hours * float(s["machine_per_h"])
    return {"material": str(s["material"]).upper(), "grams": round(grams, 1), "hours": round(hours, 2),
            "filament_cost": round(filament, 2), "power_cost": round(power, 2), "machine_cost": round(machine, 2),
            "total": round(filament + power + machine, 2)}


def fetch_freecad(log=print) -> list[str] | None:
    """Download the latest stable FreeCAD for this machine; returns the command that runs it."""
    import hashlib
    import tempfile
    import urllib.request

    system, machine = platform.system(), platform.machine().lower()
    if system == "Windows":
        if not shutil.which("winget"):
            raise RuntimeError("Install FreeCAD from https://www.freecad.org/downloads.php, then press Recheck.")
        log("Installing FreeCAD with winget…")
        subprocess.run(["winget", "install", "--id", "FreeCAD.FreeCAD", "-e", "--accept-package-agreements",
                        "--accept-source-agreements"], check=False)
        return find_freecad()
    with urllib.request.urlopen("https://api.github.com/repos/FreeCAD/FreeCAD/releases/latest", timeout=30) as r:
        rel = json.load(r)
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
    if system == "Darwin":
        arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
        want = lambda n: n.endswith(".dmg") and f"macOS-{arch}" in n  # noqa: E731
    else:
        want = lambda n: n.endswith(".AppImage") and f"Linux-{arch}" in n  # noqa: E731
    assets = {a["name"]: a["browser_download_url"] for a in rel["assets"]}
    name = next((n for n in assets if want(n)), None)
    if not name:
        raise RuntimeError(f"FreeCAD {rel['tag_name']} has no download for {system} {arch}")
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    dest = TOOLS_DIR / name
    log(f"Downloading {name} (about 800 MB)…")
    tmp = dest.with_suffix(".part")
    h = hashlib.sha256()
    with urllib.request.urlopen(assets[name], timeout=60) as r, open(tmp, "wb") as f:
        total, done, last = int(r.headers.get("Content-Length") or 0), 0, 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if total and done * 10 // total > last:
                last = done * 10 // total
                log(f"  {last * 10}%")
    sums = assets.get(name + "-SHA256.txt")
    if sums:
        with urllib.request.urlopen(sums, timeout=30) as r:
            expected = r.read().decode().split()[0].lower()
        if expected != h.hexdigest():
            tmp.unlink(missing_ok=True)
            raise RuntimeError("The FreeCAD download is corrupt (checksum mismatch); try again.")
        log("Checksum verified.")
    tmp.replace(dest)
    if system == "Darwin":
        mnt = Path(tempfile.mkdtemp())
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-quiet", "-mountpoint", str(mnt), str(dest)], check=True)
        try:
            app = next(mnt.glob("*.app"))
            target = Path.home() / "Applications" / "FreeCAD.app"
            target.parent.mkdir(exist_ok=True)
            if target.exists():
                shutil.rmtree(target)
            log(f"Copying FreeCAD to {target}…")
            shutil.copytree(app, target, symlinks=True)
        finally:
            subprocess.run(["hdiutil", "detach", "-quiet", str(mnt)], check=False)
            dest.unlink(missing_ok=True)
    else:
        dest.chmod(0o755)
        for old in TOOLS_DIR.glob("FreeCAD*.AppImage"):
            if old != dest:
                old.unlink()
    _VERSION_CACHE.clear()
    return find_freecad()
