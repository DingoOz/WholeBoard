"""Run kicad-cli: design-rule checks, manufacturing exports and renders."""

from __future__ import annotations

import csv
import json
import math
import os
import platform
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from .. import config

# Warnings that say nothing about whether the board can be made.
IGNORED_TYPES = {"lib_footprint_issues", "lib_footprint_mismatch"}


def find_kicad_cli(cfg: dict | None = None) -> str | None:
    cfg = cfg or config.load()
    configured = cfg["tools"].get("kicad_cli")
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("kicad-cli")
    if found:
        return found
    system = platform.system()
    if system == "Darwin":
        cands = ["/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"]
    elif system == "Windows":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        cands = [rf"{pf}\KiCad\{v}\bin\kicad-cli.exe" for v in ("10.0", "9.0")]
    else:
        cands = ["/usr/bin/kicad-cli", "/usr/local/bin/kicad-cli"]
    return next((c for c in cands if Path(c).exists()), None)


class KiCadError(RuntimeError):
    pass


def run(args: list[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    exe = find_kicad_cli()
    if not exe:
        raise KiCadError("kicad-cli not found. Install KiCad 9 or 10, or set its path in Settings.")
    r = subprocess.run([exe, *args], cwd=str(cwd) if cwd else None, capture_output=True,
                       text=True, timeout=timeout)
    return r


def version() -> str | None:
    try:
        r = run(["version"], timeout=120)
        return r.stdout.strip() if r.returncode == 0 else None
    except (KiCadError, OSError, subprocess.TimeoutExpired):
        return None


def drc(pcb: Path, refill: bool = True, save: bool = True) -> dict:
    """Run the DRC (optionally refilling zones and saving them). Returns a summary."""
    out = pcb.with_suffix(".drc.json")
    args = ["pcb", "drc", "--format", "json", "--severity-all", "-o", str(out)]
    if refill:
        args.append("--refill-zones")
        if save:
            args.append("--save-board")
    r = run(args + [str(pcb)], cwd=pcb.parent)
    if not out.exists():
        raise KiCadError(f"DRC failed: {r.stderr.strip() or r.stdout.strip()}")
    data = json.loads(out.read_text())
    viols = [v for v in data.get("violations", []) if v.get("type") not in IGNORED_TYPES]
    errors = [v for v in viols if v.get("severity") == "error"]
    warnings = [v for v in viols if v.get("severity") == "warning"]
    unconnected = data.get("unconnected_items", [])

    def brief(v):
        return {"type": v.get("type"), "severity": v.get("severity"),
                "description": v.get("description"),
                "items": [i.get("description") for i in v.get("items", [])][:3],
                "pos": (v.get("items") or [{}])[0].get("pos"),
                "points": [i["pos"] for i in v.get("items", [])[:3] if i.get("pos")]}

    return {
        "errors": len(errors), "warnings": len(warnings), "unconnected": len(unconnected),
        "by_type": dict(Counter(v.get("type") for v in viols)),
        "error_list": [brief(v) for v in errors[:40]],
        "warning_list": [brief(v) for v in warnings[:20]],
        "unconnected_list": [brief(v) for v in unconnected[:40]],
        "clean": not errors and not unconnected,
    }


def export_svg(pcb: Path, out: Path, layers: str, mirror: bool = False) -> Path:
    args = ["pcb", "export", "svg", "--mode-single", "--page-size-mode", "2",
            "--exclude-drawing-sheet", "-l", layers, "-o", str(out)]
    if mirror:
        args.append("--mirror")
    r = run(args + [str(pcb)], cwd=pcb.parent)
    if not out.exists():
        raise KiCadError(f"SVG export failed: {r.stderr.strip()}")
    return out


def render(pcb: Path, out: Path, view: str = "top", width: int = 1400, height: int = 1000) -> Path:
    args = ["pcb", "render", "-o", str(out), "-w", str(width), "-h", str(height),
            "--quality", "high", "--background", "opaque"]
    if view == "iso":
        args += ["--rotate", "-40,0,35", "--perspective", "--zoom", "0.9"]
    elif view == "bottom":
        args += ["--side", "bottom"]
    else:
        args += ["--side", "top"]
    r = run(args + [str(pcb)], cwd=pcb.parent)
    if not out.exists():
        raise KiCadError(f"3D render failed: {r.stderr.strip()}")
    return out


def export_fab(pcb: Path, out_dir: Path, layers: int, extra_layers: tuple[str, ...] = ()) -> dict:
    """Gerbers + drill files (zipped for JLCPCB) and the raw placement file."""
    gerb = out_dir / "gerbers"
    if gerb.exists():
        shutil.rmtree(gerb)
    gerb.mkdir(parents=True)
    cu = ["F.Cu", *[f"In{i}.Cu" for i in range(1, max(0, layers - 2) + 1)], "B.Cu"]
    lay = ",".join(cu + ["F.Paste", "B.Paste", "F.SilkS", "B.SilkS", "F.Mask", "B.Mask", "Edge.Cuts", *extra_layers])
    r = run(["pcb", "export", "gerbers", "-l", lay, "--subtract-soldermask", "--no-x2",
             "-o", str(gerb) + os.sep, str(pcb)], cwd=pcb.parent)
    if not any(gerb.iterdir()):
        raise KiCadError(f"Gerber export failed: {r.stderr.strip()}")
    r = run(["pcb", "export", "drill", "--format", "excellon", "--excellon-units", "mm",
             "--excellon-zeros-format", "decimal", "--excellon-separate-th",
             "-o", str(gerb) + os.sep, str(pcb)], cwd=pcb.parent)
    zip_base = out_dir / f"{pcb.stem}-gerbers"
    zip_path = Path(shutil.make_archive(str(zip_base), "zip", gerb))
    pos = out_dir / f"{pcb.stem}-pos.csv"
    run(["pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both",
         "--exclude-dnp", "-o", str(pos), str(pcb)], cwd=pcb.parent)
    return {"gerber_dir": str(gerb), "gerber_zip": str(zip_path),
            "pos": str(pos) if pos.exists() else None,
            "files": sorted(p.name for p in gerb.iterdir())}


# JLCPCB's parts library draws many packages turned from KiCad's zero orientation. These are the
# offsets the community tools (kicad-jlcpcb-tools, JLC2KiCad) apply, matched against the footprint
# name. A component's "cpl" entry in the spec overrides them.
CPL_ROTATIONS = [
    (r"^SOT-223", 180), (r"^SOT-23(-\d)?(_|$)", 180), (r"^SOT-89", 180), (r"^SOT-353", 180), (r"^SOT-363", 180),
    (r"^SOT-5[68]3", 180), (r"^SC-70", 180), (r"^TO-252", 180), (r"^TO-263", 180), (r"^TSOT-23", 180),
    (r"^MSOP-10_", 90), (r"^(SOIC|SO|SOP|SSOP|TSSOP|MSOP|VSSOP|TSOP|HTSSOP)-", 270),
    (r"^(.*?_|V|W|U|T)?QFN-", 270), (r"^(.*?_)?DFN-", 270), (r"^(LQFP|TQFP|QFP|VQFP)-", 270),
    (r"^CP_EIA-", 180), (r"^CP_Elec_", 180), (r"^R_Array_Convex_4x0603", 90),
    (r"^LED_WS2812B", 180), (r"^LED_SK6812", 180), (r"^XCVR_ESP32-WROVER", 270),
    (r"^PinSocket_1x0[45]_P2.54mm_Vertical", 270), (r"^PinHeader_2x05_P1.27mm_Vertical", 270),
]


def cpl_offset(package: str) -> int:
    for rx, deg in CPL_ROTATIONS:
        if re.search(rx, package or ""):
            return deg
    return 0


def jlc_cpl(pos_csv: Path, out: Path, exclude_refs: set[str] = frozenset(),
            overrides: dict | None = None) -> tuple[Path, list[dict]]:
    """Convert KiCad's position CSV into JLCPCB's CPL columns, applying JLCPCB rotation offsets.

    overrides: ref -> {"rotation": offset degrees, "dx_mm": .., "dy_mm": ..} from the spec.
    Returns the file and the corrections made."""
    rows, fixes = [], []
    overrides = overrides or {}
    with open(pos_csv, newline="") as f:
        for row in csv.DictReader(f):
            ref = row.get("Ref", "")
            if not ref or ref in exclude_refs:
                continue
            pkg = row.get("Package", "")
            top = row.get("Side", "top").lower().startswith("top")
            ov = overrides.get(ref) or {}
            off = float(ov["rotation"]) if ov.get("rotation") is not None else cpl_offset(pkg)
            rot = float(row["Rot"])
            new_rot = (rot + off) % 360 if top else (rot - off) % 360
            x, y = float(row["PosX"]), float(row["PosY"])
            dx, dy = float(ov.get("dx_mm") or 0), float(ov.get("dy_mm") or 0)
            if dx or dy:
                a = math.radians(rot)
                x += dx * math.cos(a) - dy * math.sin(a)
                y += dx * math.sin(a) + dy * math.cos(a)
            if off or dx or dy:
                fixes.append({"ref": ref, "package": pkg, "kicad_rotation": rot, "jlc_rotation": round(new_rot, 1),
                              "offset": off, "dx_mm": dx, "dy_mm": dy,
                              "source": "your spec" if ov else "JLCPCB offset table"})
            rows.append({
                "Designator": ref,
                "Mid X": f"{x:.4f}mm",
                "Mid Y": f"{y:.4f}mm",
                "Layer": "Top" if top else "Bottom",
                "Rotation": f"{new_rot:.1f}",
            })
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        w.writeheader()
        w.writerows(rows)
    return out, fixes


# ERC findings that come from how WholeBoard writes the schematic, not from the circuit.
ERC_IGNORED = {"footprint_link_issues", "lib_symbol_issues", "lib_symbol_mismatch", "endpoint_off_grid",
               "simulation_model_issue", "unannotated", "power_pin_not_driven", "footprint_filter",
               "single_global_label", "unresolved_variable"}


def erc(sch: Path) -> dict:
    """KiCad's electrical rules check on a schematic. Returns a summary."""
    out = sch.with_suffix(".erc.json")
    r = run(["sch", "erc", "--format", "json", "--severity-all", "-o", str(out), str(sch)], cwd=sch.parent)
    if not out.exists():
        raise KiCadError(f"ERC failed: {r.stderr.strip() or r.stdout.strip()}")
    data = json.loads(out.read_text())
    viols = [v for sh in data.get("sheets", []) for v in sh.get("violations", [])]
    keep = [v for v in viols if v.get("type") not in ERC_IGNORED]

    def brief(v):
        return {"type": v.get("type"), "severity": v.get("severity"), "description": v.get("description"),
                "items": [i.get("description") for i in v.get("items", [])][:4]}
    errors = [brief(v) for v in keep if v.get("severity") == "error"]
    warnings = [brief(v) for v in keep if v.get("severity") != "error"]
    return {"errors": errors[:40], "warnings": warnings[:40], "ignored": len(viols) - len(keep),
            "by_type": dict(Counter(v.get("type") for v in keep)), "clean": not errors}
