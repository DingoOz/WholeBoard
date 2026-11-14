"""Antenna simulation in the Sim Lab: the antenna, the board's ground plane, nearby parts and the enclosure, solved
with openEMS (FDTD, GPL-3.0), with and without the box.

Module antennas are proprietary shapes, so the antenna is a generic inverted-F that fits the antenna area, about a
quarter wave long at the band; chip antennas get the same. The useful result is the comparison: how far the box,
the parts and the ground plane shift the resonance and the return loss (S11). It is not a prediction of range and
does not replace the VNA measurement in the bring-up guide or certification testing.

openEMS runs from a Python that has it (python3 with `import openEMS`), or from a small Debian image WholeBoard
builds once (python -m wholeboard.doctor --install openems).
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import antenna, design

C0 = 299792458.0
FR4 = {"eps": 4.3, "tand": 0.02}
# typical relative permittivity at 2.4 GHz of printed plastics (they vary with brand, colour and infill)
PLASTIC_EPS = {"PLA": 2.75, "PETG": 2.6, "ABS": 2.5, "ASA": 2.6, "TPU": 2.9, "PC": 2.9}
IMAGE = "wholeboard-openems:1"
DOCKERFILE = ("FROM debian:bookworm-slim\nRUN apt-get update && apt-get install -y --no-install-recommends "
              "python3-openems python3-numpy python3-h5py && rm -rf /var/lib/apt/lists/*\n")
TRACE_MM = 0.5
NOTE = ("A generic inverted-F antenna stands in for the module's own antenna, sized to a quarter wave of the band. "
        "Compare the variants with each other; measure the real board with a VNA.")


def _ems(x: float, y: float, z: float) -> list[float]:
    """Board coordinates (mm, y down, z up from the board's underside) to the solver's right-handed frame."""
    return [round(x, 3), round(-y, 3), round(z, 3)]


def _box(r, z0: float, z1: float, **kw) -> dict:
    a, b = _ems(r[0], r[3], z0), _ems(r[2], r[1], z1)
    return {"a": a, "b": b, **kw}


def _minus(outer, hole) -> list[tuple]:
    """outer rectangle minus hole, as up to four rectangles."""
    h = antenna._isect(outer, hole)
    if not h:
        return [outer]
    x0, y0, x1, y1 = outer
    parts = [(x0, y0, x1, h[1]), (x0, h[3], x1, y1), (x0, h[1], h[0], h[3]), (h[2], h[1], x1, h[3])]
    return [p for p in parts if p[2] - p[0] > 0.05 and p[3] - p[1] > 0.05]


def ifa(a: dict, thick: float, f0: float) -> tuple[list[dict], dict, float]:
    """A planar inverted-F in the antenna area: a short to ground, a feed 3 mm along, and an arm parallel to the
    ground edge. Returns the metal, the feed port and the arm length."""
    r, (fx, fy) = a["antenna"], a["facing"] or (0.0, -1.0)
    lam = C0 / f0 * 1000
    quarter = lam / 4 / math.sqrt((1 + FR4["eps"]) / 2)
    z = thick
    along_x = fx == 0                                # the arm runs along x when the antenna faces up or down
    width = (r[2] - r[0]) if along_x else (r[3] - r[1])
    depth = (r[3] - r[1]) if along_x else (r[2] - r[0])
    h = max(1.5, min(depth - 0.8, 5.0))              # arm height above the ground edge
    arm = max(4.0, min(width - 1.0, quarter - h))
    # local frame: u along the ground edge, v away from it (towards the antenna's end)
    if along_x:
        g = r[3] if fy < 0 else r[1]                  # the ground edge is the antenna area's back
        sv = -1.0 if fy < 0 else 1.0
        u0 = r[0] + 0.5

        def P(u, v):
            return (u0 + u, g + sv * v)
    else:
        g = r[0] if fx > 0 else r[2]
        sv = 1.0 if fx > 0 else -1.0
        u0 = r[1] + 0.5

        def P(u, v):
            return (g + sv * v, u0 + u)

    def strip(u0_, v0_, u1_, v1_):
        p0, p1 = P(u0_, v0_), P(u1_, v1_)
        rr = (min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]), max(p0[1], p1[1]))
        return _box(rr, z, z, fine=True)
    w = TRACE_MM
    metal = [strip(0, 0, w, h + w),                   # short to ground
             strip(0, h, arm, h + w),                 # arm
             strip(3.0, 1.0, 3.0 + w, h)]             # feed leg, from 1 mm above the ground edge
    p0, p1 = P(3.0, 0.0), P(3.0 + w, 1.0)
    port = _box((min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]), max(p0[1], p1[1])), z, z,
                ohms=50, dir="y" if along_x else "x")
    return metal, port, round(arm + h, 1)


def build_model(spec: dict, geo: dict, ref: str | None = None, box: dict | None = None) -> dict:
    """The openEMS model of one antenna, its board and (when box is given) its enclosure.

    box: {"cavity": [x0, y0, x1, y1] in board coordinates, "wall", "floor", "lid" (thicknesses, mm),
    "z_floor", "z_lid" (inside floor and lid heights relative to the board's underside), "material"}."""
    spec = design.normalise(spec)
    ants = [a for a in antenna.find_antennas(spec, geo) if a["kind"] != "connector" and (ref is None or a["ref"] == ref)]
    if not ants:
        raise ValueError("there is no on-board antenna to simulate" + (f" called {ref}" if ref else ""))
    a = ants[0]
    lo_mhz, hi_mhz = a["band_mhz"]
    f0 = (lo_mhz + hi_mhz) / 2 * 1e6
    thick = float(spec["board"].get("thickness_mm") or 1.6)
    bb = geo["bbox"]
    board = (bb[0], bb[1], bb[2], bb[3])
    clear = a["keepout"] or a["antenna"]
    metal: list[dict] = []
    gnd = []
    for rr in _minus(board, clear):
        gnd += [_box(rr, 0, 0), _box(rr, thick, thick)]          # ground on both outer layers outside the keep-out
    if a["body"] and a["body"] != a["antenna"]:
        rest = antenna._rest_of_body(a)
        if rest:
            gnd += [_box(rest, thick, thick)]
    metal += gnd
    arm_metal, port, length = ifa(a, thick, f0)
    metal += arm_metal
    dielectrics = [_box(board, 0, thick, **FR4, priority=1)]
    parts = []
    for o in antenna._part_obstacles(spec, geo, a, None):
        if not o["metal"] or antenna.rect_gap(a["antenna"], o["rect"]) > 40:
            continue
        z0, z1 = (thick, thick + o["height_mm"]) if o["side"] == "top" else (-o["height_mm"], 0.0)
        metal.append(_box(o["rect"], z0, z1, priority=10))
        parts.append(o["ref"])
    variants = [{"name": "board", "title": "Bare board"}]
    if box:
        variants.append({"name": "boxed", "title": f"In the {box.get('material', 'PLA')} enclosure",
                         **_box_parts(box)})
    # the solver's domain: everything plus a quarter wave of air
    lam = C0 / f0 * 1000
    xs = [board[0], board[2]] + ([box["cavity"][0] - box["wall"], box["cavity"][2] + box["wall"]] if box else [])
    ys = [board[1], board[3]] + ([box["cavity"][1] - box["wall"], box["cavity"][3] + box["wall"]] if box else [])
    zs = [-6.0, thick + 6.0] + ([box["z_floor"] - box["floor"], box["z_lid"] + box["lid"]] if box else [])
    m = lam / 4
    domain = [_ems(min(xs) - m, max(ys) + m, min(zs) - m), _ems(max(xs) + m, min(ys) - m, max(zs) + m)]
    return {"ref": a["ref"], "footprint": a["footprint"], "band_mhz": [lo_mhz, hi_mhz], "f0_hz": f0,
            "fc_hz": f0 * 0.4, "f_start_hz": f0 * 0.7, "f_stop_hz": f0 * 1.3, "metal": metal,
            "dielectrics": dielectrics, "port": port, "variants": variants, "domain": domain,
            "coarse_mm": round(C0 / (f0 * 1.3) * 1000 / 20, 2), "fine_mm": 0.5, "max_steps": 40000,
            "end_criteria": 1e-4, "center": _ems((a["antenna"][0] + a["antenna"][2]) / 2,
                                                  (a["antenna"][1] + a["antenna"][3]) / 2, thick),
            "antenna_length_mm": length, "parts_as_metal": parts, "note": NOTE, "board_mm": thick}


def _box_parts(box: dict) -> dict:
    """The enclosure as six slabs: plastic dielectric, or metal for a metal case."""
    c, w, fl, ld = box["cavity"], box["wall"], box["floor"], box["lid"]
    z0, z1 = box["z_floor"], box["z_lid"]
    outer = (c[0] - w, c[1] - w, c[2] + w, c[3] + w)
    slabs = [(outer, z0 - fl, z0), (outer, z1, z1 + ld),
             ((outer[0], outer[1], c[0], outer[3]), z0, z1), ((c[2], outer[1], outer[2], outer[3]), z0, z1),
             ((c[0], outer[1], c[2], c[1]), z0, z1), ((c[0], c[3], c[2], outer[3]), z0, z1)]
    mat = str(box.get("material") or "PLA")
    if re.search(r"alu|metal|steel", mat, re.I):
        return {"metal": [_box(r, a, b, priority=8) for r, a, b in slabs]}
    eps = PLASTIC_EPS.get(mat.upper(), 2.7)
    return {"dielectrics": [_box(r, a, b, eps=eps, tand=0.01, priority=2) for r, a, b in slabs]}


def box_from_job(job: dict, origin: tuple[float, float], board_h: float, material: str) -> dict:
    """The box description build_model wants, from a resolved printed enclosure (enclosure.resolve)."""
    wall, b = job["wall"], job["board"]
    ox, oy = origin
    c0x, c1y = wall - b["x"], b["y"] + board_h - wall
    return {"cavity": [ox + c0x, oy + c1y - job["cavity"][3], ox + c0x + job["cavity"][2], oy + c1y],
            "wall": wall, "floor": job["floor"], "lid": job["lid_t"], "z_floor": job["floor"] - job["z_board"],
            "z_lid": job["base_h"] - job["z_board"], "material": material}


def box_from_case(c: dict, origin: tuple[float, float], material: str) -> dict:
    """The same for a stock case (case.layout)."""
    ox, oy = origin
    return {"cavity": [ox - c["ax"], oy - c["ay"], ox - c["ax"] + c["L"], oy - c["ay"] + c["W"]], "wall": c["wall"],
            "floor": c["floor"], "lid": c["wall"], "z_floor": -c["standoff"], "z_lid": c["H"] - c["standoff"],
            "material": material}


# ----------------------------------------------------------------------------------- running openEMS
def native_python() -> str | None:
    for py in (os.environ.get("WHOLEBOARD_OPENEMS_PYTHON"), sys.executable, shutil.which("python3")):
        if py and subprocess.run([py, "-c", "import openEMS, CSXCAD"], capture_output=True).returncode == 0:
            return py
    return None


def _image_ready() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0


def command() -> list[str] | None:
    """How to run the solver script here, or None when openEMS is not available."""
    py = native_python()
    if py:
        return [py]
    if _image_ready() and hasattr(os, "getuid"):
        home = str(Path.home())
        return ["docker", "run", "--rm", "-u", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
                "-v", f"{home}:{home}", "-w", "/tmp", IMAGE, "python3"]
    return None


def available() -> bool:
    return command() is not None


def install(log=print) -> bool:
    """Build the openEMS image (Debian's python3-openems)."""
    if native_python():
        log("openEMS is already importable from Python.")
        return True
    if not shutil.which("docker"):
        log("Install openEMS (https://docs.openems.de) or Docker, then try again.")
        return False
    log(f"Building the {IMAGE} image (Debian bookworm with python3-openems)")
    r = subprocess.run(["docker", "build", "-t", IMAGE, "-"], input=DOCKERFILE, text=True, capture_output=True)
    if r.returncode != 0:
        log(r.stderr[-2000:])
    return r.returncode == 0


def run(model: dict, out_dir: Path, log=print, cancel=None, timeout: int = 3600) -> dict:
    """Solve every variant of the model; returns the results with a short comparison."""
    cmd = command()
    if not cmd:
        return {"ok": False, "available": False,
                "error": "openEMS is not installed. Install it, or run: python -m wholeboard.doctor --install openems"}
    out_dir.mkdir(parents=True, exist_ok=True)
    mf = out_dir / "model.json"
    mf.write_text(json.dumps(model))
    script = Path(__file__).with_name("antenna_ems.py")
    if cmd[0] == "docker":
        out = str(out_dir.resolve())
        cmd = [*cmd[:2], "-v", f"{out}:{out}", *cmd[2:]]      # the board's folder may be outside the home folder
        local = out_dir / "antenna_ems.py"
        shutil.copyfile(script, local)     # inside the home folder, which the container sees
        script = local
    proc = subprocess.Popen([*cmd, str(script), str(mf), str(out_dir)], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    tail: list[str] = []
    import threading
    stop = threading.Event()

    def watch():
        while not stop.wait(0.5):
            if cancel is not None and cancel.is_set():
                proc.kill()
                return
    threading.Thread(target=watch, daemon=True).start()
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                tail = (tail + [line])[-30:]
                if line.startswith(("solving", "done")):
                    log(line)
        proc.wait(timeout=timeout)
    finally:
        stop.set()
    res_file = out_dir / "results.json"
    if proc.returncode != 0 or not res_file.exists():
        return {"ok": False, "available": True, "error": "openEMS failed: " + " / ".join(tail[-6:])}
    res = json.loads(res_file.read_text())
    return {**res, **summarise(model, res)}


def summarise(model: dict, res: dict) -> dict:
    lo, hi = model["band_mhz"]
    rows, notes = [], []
    for v in res["variants"]:
        f, s = v["freq_mhz"], v["s11_db"]
        in_band = [x for fx, x in zip(f, s) if lo <= fx <= hi]
        worst = max(in_band) if in_band else None
        below = [fx for fx, x in zip(f, s) if x <= -10]
        rows.append({"variant": v["title"], "resonance_mhz": v["resonance_mhz"], "s11_min_db": v["s11_min_db"],
                     "worst_in_band_db": round(worst, 1) if worst is not None else None,
                     "bandwidth_10db_mhz": round(max(below) - min(below), 1) if below else 0.0})
    if len(rows) > 1:
        shift = rows[-1]["resonance_mhz"] - rows[0]["resonance_mhz"]
        notes.append(f"The enclosure moves the resonance by {shift:+.0f} MHz"
                     + (" (down: plastic near the antenna)" if shift < -5 else "") + ".")
        if rows[-1]["worst_in_band_db"] is not None and rows[-1]["worst_in_band_db"] > -10:
            notes.append(f"In the box the return loss is only {rows[-1]['worst_in_band_db']:g} dB at the worst point "
                         f"of {lo:g}–{hi:g} MHz (aim for -10 dB or better): give the antenna more room, or re-tune "
                         "the matching network after measuring.")
    return {"ok": True, "available": True, "rows": rows, "notes": notes}


DOC = " ".join(__doc__.split("\n\n")[0].split())
